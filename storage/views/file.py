"""
File management views for the storage app.
"""

import os
import traceback
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from django.conf import settings
from ..models import File, Chunk, Node
from ..utils import (
    chunk_file, get_s3_client, get_least_loaded_nodes, create_bucket_if_not_exists,
    is_file_cached, get_cache_path
)


class FileListView(APIView):
    """
    API endpoint for listing user's files.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        files = File.objects.filter(owner=request.user)
        data = [{
            'id': file.id,
            'name': file.name,
            'size': file.size,
            'upload_date': file.upload_date,
            'chunks': file.chunks.count()
        } for file in files]
        
        return Response(data)
        
    def delete(self, request, file_id=None):
        """Delete a file and all its chunks from storage and database"""
        if not file_id:
            return Response({'error': 'No file ID provided'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            file_obj = get_object_or_404(File, id=file_id, owner=request.user)
            
            print(f"\n=== DELETING FILE: {file_obj.name} (ID: {file_id}) ===")
            
            # Get all chunks for this file
            chunks = file_obj.chunks.all()
            chunk_count = chunks.count()
            
            # Delete each chunk from storage nodes
            for chunk in chunks:
                print(f"Deleting chunk {chunk.chunk_number} from storage...")
                
                # Get all nodes that have this chunk
                for node in chunk.nodes.all():
                    try:
                        s3_client = get_s3_client(node.url)
                        chunk_key = f'{file_id}/{chunk.chunk_number}'
                        
                        # Delete chunk from storage
                        s3_client.delete_object(
                            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                            Key=chunk_key
                        )
                        
                        # Update node stats
                        node.storage_usage -= chunk.size
                        if node.storage_usage < 0:
                            node.storage_usage = 0
                        node.load -= 1
                        if node.load < 0:
                            node.load = 0
                        node.save()
                        
                        print(f"✓ Deleted from node {node.url}")
                    except Exception as e:
                        print(f"Error deleting from node {node.url}: {str(e)}")
                
                # Delete chunk from cache if it exists
                if is_file_cached(file_id):
                    try:
                        os.remove(get_cache_path(file_id))
                        print(f"✓ Removed file from cache")
                    except Exception as cache_error:
                        print(f"Error removing from cache: {str(cache_error)}")
            
            # Finally, delete the file from database (this will cascade delete chunks)
            file_obj.delete()
            print(f"✓ Deleted file record and all {chunk_count} chunks")
            print("=== FILE DELETION COMPLETED ===\n")
            
            return Response({'status': 'success', 'message': f'File {file_obj.name} and all chunks deleted'})
            
        except File.DoesNotExist:
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': f'Error deleting file: {str(e)}'}, 
                          status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@login_required
def file_upload_view(request):
    """
    View for file upload form.
    """
    if request.method == 'POST':
        print("\n=== WEB UPLOAD VIEW STARTED - DIRECT IMPLEMENTATION ===")
        if 'file' not in request.FILES:
            print("ERROR: No file in request.FILES")
            print(f"request.FILES: {request.FILES}")
            print(f"request.POST: {request.POST}")
            messages.error(request, 'No file was provided.')
            return render(request, 'storage/file/upload.html')
        
        try:
            uploaded_file = request.FILES['file']
            print(f"File received: {uploaded_file.name}, size: {uploaded_file.size}")
            
            # Create file record directly
            file_obj = File.objects.create(
                name=uploaded_file.name,
                size=uploaded_file.size,
                owner=request.user
            )
            print(f"File record created: {file_obj.id}")
            
            # Get nodes
            available_nodes = list(Node.objects.filter(status='online'))
            print(f"Available nodes: {len(available_nodes)}")
            
            if not available_nodes:
                print("No nodes found, creating default nodes")
                # Create default nodes if none exist
                for url in ['http://localhost:9000', 'http://localhost:9002', 'http://localhost:9004']:
                    node, created = Node.objects.get_or_create(url=url, defaults={'status': 'online'})
                    print(f"Node {url}: {'created' if created else 'already exists'}")
                available_nodes = list(Node.objects.filter(status='online'))
                print(f"Available nodes after creation: {len(available_nodes)}")
            
            if len(available_nodes) < 1:
                print("ERROR: No storage nodes available after creation attempt")
                file_obj.delete()
                messages.error(request, 'No storage nodes available')
                return render(request, 'storage/file/upload.html')
            
            # Display all node info
            for node in available_nodes:
                print(f"Node info: {node.url}, status: {node.status}, load: {node.load}")
            
            # Split file into chunks
            print(f"Chunking file {uploaded_file.name}")
            chunks_data = chunk_file(uploaded_file)
            print(f"File split into {len(chunks_data)} chunks")
            
            # Process each chunk
            for i, (chunk_data, checksum, size) in enumerate(chunks_data):
                print(f"\nProcessing chunk {i}, size: {size}, checksum: {checksum[:8]}...")
                
                # Check for deduplication by checksum
                existing_chunk = Chunk.objects.filter(checksum=checksum, size=size).first()
                
                if existing_chunk:
                    print(f"Found existing chunk with same checksum, reusing nodes")
                    # Create a new chunk record but link to the same nodes
                    chunk_obj = Chunk.objects.create(
                        file=file_obj,
                        chunk_number=i,
                        checksum=checksum,
                        size=size
                    )
                    # Use the same nodes as the existing chunk
                    existing_nodes = list(existing_chunk.nodes.all())
                    print(f"Reusing {len(existing_nodes)} nodes from existing chunk")
                    chunk_obj.nodes.set(existing_nodes)
                    continue
                
                # Get the least loaded nodes for storing the chunk
                node_count = min(2, len(available_nodes))
                nodes = get_least_loaded_nodes(n=node_count) if node_count > 0 else available_nodes[:1]
                print(f"Selected {len(nodes)} nodes for chunk storage")
                for node in nodes:
                    print(f"- Node: {node.url}")
                
                # Create chunk record
                chunk_obj = Chunk.objects.create(
                    file=file_obj,
                    chunk_number=i,
                    checksum=checksum,
                    size=size
                )
                print(f"Created chunk record: {chunk_obj.id}")
                chunk_obj.nodes.set(nodes)
                
                # Upload chunk to selected nodes
                upload_success = False
                for node in nodes:
                    try:
                        print(f"Attempting upload to node: {node.url}")
                        s3_client = get_s3_client(node.url)
                        print(f"S3 client created for node: {node.url}")
                        
                        bucket_name = create_bucket_if_not_exists(s3_client)
                        print(f"Using bucket: {bucket_name}")
                        
                        # Upload the chunk
                        chunk_data.seek(0)
                        chunk_key = f'{file_obj.id}/{i}'
                        print(f"Uploading chunk with key: {chunk_key}")
                        
                        # The actual upload
                        s3_client.upload_fileobj(chunk_data, bucket_name, chunk_key)
                        print(f"Successfully uploaded chunk to {node.url}")
                        
                        # Update node load
                        node.load += 1
                        node.storage_usage += size
                        node.save()
                        print(f"Updated node load: {node.load}, storage: {node.storage_usage}")
                        upload_success = True
                        break  # Once we successfully upload to one node, we can move on
                    except Exception as e:
                        # Log the error but try the next node
                        print(f"ERROR uploading to node {node.url}: {str(e)}")
                        print(f"Error type: {type(e).__name__}")
                        continue
                
                if not upload_success:
                    # If we couldn't upload to any node, fail the whole operation
                    error_msg = f"Failed to upload chunk {i} to any node"
                    print(f"ERROR: {error_msg}")
                    file_obj.delete()  # Clean up
                    messages.error(request, error_msg)
                    return render(request, 'storage/file/upload.html')
            
            print(f"\nUpload completed successfully: {file_obj.name}")
            print(f"Total chunks: {file_obj.chunks.count()}")
            print("=== UPLOAD PROCESS COMPLETED ===\n")
            
            messages.success(request, f'File "{uploaded_file.name}" uploaded successfully')
            return redirect('file_list_view')
            
        except Exception as e:
            # Global exception handler
            print(f"ERROR in upload process: {str(e)}")
            print(f"Error type: {type(e).__name__}")
            
            # Get traceback for more details
            print("Traceback:")
            traceback.print_exc()
            
            # Clean up if we created a file record
            if 'file_obj' in locals() and file_obj:
                print(f"Deleting file record: {file_obj.id}")
                file_obj.delete()
            
            messages.error(request, f'Error uploading file: {str(e)}')
            print("=== UPLOAD PROCESS FAILED ===\n")
        
    return render(request, 'storage/file/upload.html')


@login_required
def file_list_view(request):
    """
    View for listing user's files.
    """
    # Handle system reset request (admin only)
    if request.method == 'POST' and 'reset_system' in request.POST:
        if request.user.is_superuser:
            import subprocess
            try:
                # Run the reset script with force flag to skip confirmation
                subprocess.run(['python3', 'reset_system.py', '--force'], check=True)
                messages.success(request, 'System reset completed successfully.')
            except subprocess.CalledProcessError as e:
                messages.error(request, f'System reset failed: {str(e)}')
        else:
            messages.error(request, 'Only administrators can reset the system.')
    
    files = File.objects.filter(owner=request.user).order_by('-upload_date')
    return render(request, 'storage/file/list.html', {
        'files': files,
        'is_admin': request.user.is_superuser
    })


@login_required
def file_detail_view(request, file_id):
    """
    View for file details, showing chunks and storage distribution.
    """
    file = get_object_or_404(File, id=file_id, owner=request.user)
    
    # Handle file deletion
    if request.method == 'POST' and 'delete' in request.POST:
        # Create API client to handle deletion
        api_client = APIClient()
        api_client.force_authenticate(user=request.user)
        
        try:
            # Call the delete endpoint
            response = api_client.delete(
                reverse('api_file_detail', args=[file_id])
            )
            
            if response.status_code == 200:
                messages.success(request, f'File "{file.name}" deleted successfully.')
                return redirect('file_list_view')
            else:
                error_msg = response.data.get('error', 'Unknown error')
                messages.error(request, f'Error deleting file: {error_msg}')
        except Exception as e:
            messages.error(request, f'Error deleting file: {str(e)}')
    
    chunks = file.chunks.all().order_by('chunk_number')
    
    # Get storage distribution data
    nodes = Node.objects.all()
    
    # Add counts of chunks per node
    for node in nodes:
        node.chunks_count = node.chunks.filter(file=file).count()
    
    return render(request, 'storage/file/detail.html', {
        'file': file,
        'chunks': chunks,
        'nodes': nodes,
    })
