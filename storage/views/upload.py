"""
Upload views for the storage app.
"""

from django.conf import settings
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
import traceback
from io import BytesIO
import hashlib

from ..models import File, Chunk, Node
from ..utils import (
    chunk_file, get_s3_client, get_least_loaded_nodes, create_bucket_if_not_exists,
    get_storage_bucket_names
)


class UploadView(APIView):
    """
    API endpoint for file uploads.
    Chunks files and distributes them across MinIO nodes.
    Features:
    - Redundant storage across multiple nodes
    - Deduplication with verification
    - Consistent bucket usage
    - Robust error handling
    """
    permission_classes = [IsAuthenticated]
    
    def verify_chunk_on_node(self, node, bucket_name, chunk_key, expected_checksum, chunk_data=None):
        """
        Verify that a chunk exists on a node and has the correct checksum.
        Returns True if the chunk is verified, False otherwise.
        """
        try:
            print(f"Verifying chunk on node {node.url}, bucket {bucket_name}, key {chunk_key}")
            s3_client = get_s3_client(node.url)
            
            # First check if the object exists
            try:
                s3_client.head_object(Bucket=bucket_name, Key=chunk_key)
                print(f"✓ Object exists on node {node.url}")
            except Exception as e:
                print(f"Object not found on node {node.url}: {str(e)}")
                return False
            
            # If we have the chunk data, we can verify the checksum directly
            if chunk_data:
                chunk_data.seek(0)
                return True
            
            # Otherwise, download the chunk and verify the checksum
            try:
                chunk_buffer = BytesIO()
                s3_client.download_fileobj(bucket_name, chunk_key, chunk_buffer)
                chunk_buffer.seek(0)
                downloaded_data = chunk_buffer.read()
                
                # Verify checksum
                downloaded_checksum = hashlib.sha256(downloaded_data).hexdigest()
                if downloaded_checksum != expected_checksum:
                    print(f"Checksum mismatch on node {node.url}")
                    print(f"Expected: {expected_checksum}")
                    print(f"Received: {downloaded_checksum}")
                    return False
                
                print(f"✓ Checksum verified on node {node.url}")
                return True
            except Exception as e:
                print(f"Error downloading chunk from node {node.url}: {str(e)}")
                return False
        except Exception as e:
            print(f"Error verifying chunk on node {node.url}: {str(e)}")
            return False

    def post(self, request):
        print("\n=== UPLOAD PROCESS STARTED ===")
        print(f"Request user: {request.user}")
        print(f"Request content type: {request.content_type}")
        print(f"Request headers: {dict(request.headers)}")
        
        try:
            if 'file' not in request.FILES:
                print("ERROR: No file in request.FILES")
                print(f"request.FILES: {request.FILES}")
                print(f"request.POST: {request.POST}")
                print(f"request.data: {request.data}")
                return Response({'error': 'No file provided'}, status=status.HTTP_400_BAD_REQUEST)
            
            uploaded_file = request.FILES['file']
            print(f"File received: {uploaded_file.name}, size: {uploaded_file.size}")
            
            # Create file record
            file_obj = File.objects.create(
                name=uploaded_file.name,
                size=uploaded_file.size,
                owner=request.user
            )
            print(f"File record created: {file_obj.id}")
            
            # Get nodes before chunking the file
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
                return Response({'error': 'No storage nodes available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            
            # Display all node info
            for node in available_nodes:
                print(f"Node info: {node.url}, status: {node.status}, load: {node.load}")
            
            # Split file into chunks
            print(f"Chunking file {uploaded_file.name}")
            chunks_data = chunk_file(uploaded_file)
            print(f"File split into {len(chunks_data)} chunks")
            
            # Define minimum required successful uploads
            min_required_nodes = min(2, len(available_nodes))
            print(f"Minimum required successful uploads per chunk: {min_required_nodes}")
            
            # Process each chunk
            for i, (chunk_data, checksum, size) in enumerate(chunks_data):
                print(f"\nProcessing chunk {i}, size: {size}, checksum: {checksum[:8]}...")
                try:
                    # Check for deduplication by checksum
                    existing_chunk = Chunk.objects.filter(checksum=checksum, size=size).first()
                    
                    if existing_chunk:
                        print(f"Found existing chunk with same checksum, verifying before reusing")
                        # Get nodes from the existing chunk
                        existing_nodes = list(existing_chunk.nodes.all())
                        print(f"Existing chunk has {len(existing_nodes)} nodes")
                        
                        # Verify that at least one node has the chunk and is accessible
                        verified_nodes = []
                        for node in existing_nodes:
                            if node.status == 'online':
                                # Check if the chunk is actually on this node
                                chunk_key = f'{existing_chunk.file.id}/{existing_chunk.chunk_number}'
                                
                                # Try all potential bucket names
                                for bucket_name in get_storage_bucket_names():
                                    if self.verify_chunk_on_node(node, bucket_name, chunk_key, checksum):
                                        verified_nodes.append(node)
                                        break
                        
                        if verified_nodes:
                            print(f"Verified chunk exists on {len(verified_nodes)} nodes, reusing")
                            # Create a new chunk record but link to the verified nodes
                            chunk_obj = Chunk.objects.create(
                                file=file_obj,
                                chunk_number=i,
                                checksum=checksum,
                                size=size
                            )
                            chunk_obj.nodes.set(verified_nodes)
                            
                            # If we don't have enough verified nodes, upload to additional nodes
                            if len(verified_nodes) < min_required_nodes:
                                print(f"Only {len(verified_nodes)} verified nodes, need at least {min_required_nodes}")
                                # Get additional nodes that don't already have the chunk
                                additional_nodes = [node for node in available_nodes if node not in verified_nodes]
                                additional_nodes.sort(key=lambda x: x.load)  # Sort by load
                                
                                # How many more nodes do we need?
                                nodes_needed = min_required_nodes - len(verified_nodes)
                                additional_nodes = additional_nodes[:nodes_needed]
                                
                                if additional_nodes:
                                    print(f"Uploading to {len(additional_nodes)} additional nodes")
                                    # Upload to additional nodes
                                    successful_uploads = self.upload_to_nodes(
                                        chunk_data, file_obj.id, i, checksum, size, additional_nodes
                                    )
                                    
                                    # Add successful nodes to the chunk
                                    for node in successful_uploads:
                                        if node not in verified_nodes:
                                            chunk_obj.nodes.add(node)
                                            print(f"Added node {node.url} to chunk")
                            
                            continue
                        else:
                            print(f"Could not verify existing chunk on any node, will upload as new")
                    
                    # Get the least loaded nodes for storing the chunk
                    target_node_count = min(min_required_nodes + 1, len(available_nodes))  # +1 for redundancy
                    nodes = get_least_loaded_nodes(n=target_node_count)
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
                    
                    # Upload chunk to selected nodes
                    successful_uploads = self.upload_to_nodes(
                        chunk_data, file_obj.id, i, checksum, size, nodes
                    )
                    
                    # Check if we have enough successful uploads
                    if len(successful_uploads) < min_required_nodes:
                        error_msg = f"Failed to upload chunk {i} to enough nodes. Got {len(successful_uploads)}, need {min_required_nodes}"
                        print(f"ERROR: {error_msg}")
                        raise Exception(error_msg)
                    
                    # Set the nodes for this chunk
                    chunk_obj.nodes.set(successful_uploads)
                    print(f"Set {len(successful_uploads)} nodes for chunk {i}")
                        
                except Exception as chunk_error:
                    # Clean up on chunk failure
                    print(f"ERROR during chunk processing: {str(chunk_error)}")
                    print(f"Error type: {type(chunk_error).__name__}")
                    print("Deleting file and all chunks due to error")
                    file_obj.delete()  # This will cascade delete the chunks
                    return Response({'error': f'Chunk processing error: {str(chunk_error)}'}, 
                                   status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            print(f"\nUpload completed successfully: {file_obj.name}")
            print(f"Total chunks: {file_obj.chunks.count()}")
            print("=== UPLOAD PROCESS COMPLETED ===\n")
            
            return Response({
                'id': file_obj.id,
                'name': file_obj.name,
                'size': file_obj.size,
                'chunks': file_obj.chunks.count()
            }, status=status.HTTP_201_CREATED)
            
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
            
            print("=== UPLOAD PROCESS FAILED ===\n")
            return Response({'error': f'Upload failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def upload_to_nodes(self, chunk_data, file_id, chunk_number, checksum, size, nodes):
        """
        Upload a chunk to multiple nodes and return a list of nodes where the upload was successful.
        """
        successful_nodes = []
        
        for node in nodes:
            try:
                print(f"Attempting upload to node: {node.url}")
                s3_client = get_s3_client(node.url)
                print(f"S3 client created for node: {node.url}")
                
                # Use the primary bucket name
                bucket_name = settings.AWS_STORAGE_BUCKET_NAME
                
                # Ensure the bucket exists
                try:
                    s3_client.head_bucket(Bucket=bucket_name)
                    print(f"Bucket {bucket_name} exists on node {node.url}")
                except Exception as e:
                    print(f"Bucket {bucket_name} doesn't exist on node {node.url}, creating it")
                    bucket_name = create_bucket_if_not_exists(s3_client)
                
                # Upload the chunk
                chunk_data.seek(0)
                chunk_key = f'{file_id}/{chunk_number}'
                print(f"Uploading chunk with key: {chunk_key}")
                
                # The actual upload
                s3_client.upload_fileobj(chunk_data, bucket_name, chunk_key)
                
                # Verify the upload was successful
                if self.verify_chunk_on_node(node, bucket_name, chunk_key, checksum, chunk_data):
                    print(f"Successfully uploaded and verified chunk to {node.url}")
                    
                    # Update node load
                    node.load += 1
                    node.storage_usage += size
                    node.last_check = timezone.now()  # Update last check time
                    node.save()
                    print(f"Updated node load: {node.load}, storage: {node.storage_usage}")
                    
                    successful_nodes.append(node)
                else:
                    print(f"Upload verification failed for node {node.url}")
            except Exception as e:
                # Log the error but try the next node
                print(f"ERROR uploading to node {node.url}: {str(e)}")
                print(f"Error type: {type(e).__name__}")
                
                # Try to get more details if it's a boto3 error
                if hasattr(e, 'response'):
                    print(f"Response status: {e.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
                    print(f"Error response: {e.response}")
                continue
        
        return successful_nodes
