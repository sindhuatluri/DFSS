"""
Cache management views for the storage app.
"""

import os
import hashlib
from io import BytesIO
from django.core.cache import cache
from django.utils import timezone
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from ..models import File, Chunk
from ..utils import (
    get_s3_client, is_file_cached, get_cache_path, cache_file,
    get_storage_bucket_names, find_alternate_chunk_sources, CACHE_DIR
)


class CacheManagementView(APIView):
    """
    API endpoint for managing the file cache system.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get cache statistics"""
        import os
        
        # Count cached files
        cached_files = []
        cache_size = 0
        
        if os.path.exists(CACHE_DIR):
            for filename in os.listdir(CACHE_DIR):
                if filename.startswith('file_'):
                    file_path = os.path.join(CACHE_DIR, filename)
                    file_size = os.path.getsize(file_path)
                    file_id = int(filename.replace('file_', ''))
                    
                    # Get file metadata if available
                    file_obj = None
                    try:
                        file_obj = File.objects.get(id=file_id)
                    except File.DoesNotExist:
                        pass
                    
                    # Get access stats
                    access_count = cache.get(f"file_access_count_{file_id}", 0)
                    last_access = cache.get(f"file_last_access_{file_id}", 0)
                    
                    cached_files.append({
                        'file_id': file_id,
                        'name': file_obj.name if file_obj else 'Unknown',
                        'size': file_size,
                        'access_count': access_count,
                        'last_access': last_access
                    })
                    
                    cache_size += file_size
        
        # Get cache hit rate if we have access data
        cache_hits = 0
        cache_total = 0
        
        for file in File.objects.all():
            access_count = cache.get(f"file_access_count_{file.id}", 0)
            if access_count > 0:
                cache_total += access_count
                if is_file_cached(file.id):
                    # We count each access after the first as a potential cache hit
                    cache_hits += max(0, access_count - 1)
        
        cache_hit_rate = (cache_hits / cache_total * 100) if cache_total > 0 else 0
        
        # Sort by access count (most accessed first)
        cached_files.sort(key=lambda x: x['access_count'], reverse=True)
        
        return Response({
            'cached_files_count': len(cached_files),
            'cache_size_bytes': cache_size,
            'cache_hit_rate': round(cache_hit_rate, 1),
            'cached_files': cached_files[:20]  # Limit to top 20
        })
    
    def post(self, request):
        """Manage cache operations"""
        operation = request.data.get('operation')
        
        if operation == 'clear_all':
            # Clear all cached files
            import os, shutil
            if os.path.exists(CACHE_DIR):
                for filename in os.listdir(CACHE_DIR):
                    file_path = os.path.join(CACHE_DIR, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
            return Response({'status': 'success', 'message': 'Cache cleared successfully'})
            
        elif operation == 'cache_file':
            # Manually cache a file
            file_id = request.data.get('file_id')
            if not file_id:
                return Response({'error': 'No file_id provided'}, status=status.HTTP_400_BAD_REQUEST)
                
            try:
                file_obj = File.objects.get(id=file_id)
                
                # Check if already cached
                if is_file_cached(file_id):
                    return Response({'status': 'success', 'message': 'File already cached'})
                
                # Reconstruct the file from chunks and cache it
                file_buffer = BytesIO()
                chunks = file_obj.chunks.all().order_by('chunk_number')
                
                for chunk in chunks:
                    # Get nodes for this chunk
                    nodes = list(chunk.nodes.filter(status='online'))
                    
                    # Try to get chunk data from primary nodes
                    chunk_data = None
                    error = None
                    tried_nodes = list(nodes)  # Track nodes we've tried
                    
                    for node in nodes:
                        try:
                            s3_client = get_s3_client(node.url)
                            chunk_key = f'{file_obj.id}/{chunk.chunk_number}'
                            
                            # Try all potential bucket names with this node
                            bucket_name = settings.AWS_STORAGE_BUCKET_NAME  # Default
                            bucket_found = False
                            
                            for potential_bucket in get_storage_bucket_names():
                                try:
                                    print(f"Checking if object exists: bucket={potential_bucket}, key={chunk_key}")
                                    s3_client.head_object(Bucket=potential_bucket, Key=chunk_key)
                                    print(f"✓ Object found in bucket: {potential_bucket}")
                                    bucket_name = potential_bucket
                                    bucket_found = True
                                    break
                                except Exception as e:
                                    print(f"Object not found in bucket {potential_bucket}")
                            
                            # If we couldn't find the object in any bucket, skip this node
                            if not bucket_found:
                                print(f"Could not find chunk in any bucket on node {node.url}")
                                continue
                            
                            # Download chunk
                            chunk_buffer = BytesIO()
                            print(f"Downloading from bucket={bucket_name}, key={chunk_key}")
                            s3_client.download_fileobj(
                                bucket_name, 
                                chunk_key, 
                                chunk_buffer
                            )
                            chunk_buffer.seek(0)
                            chunk_data = chunk_buffer.read()
                            
                            # Verify checksum
                            if hashlib.sha256(chunk_data).hexdigest() == chunk.checksum:
                                break
                        except Exception as e:
                            error = str(e)
                            continue
                    
                    # If primary nodes failed, search for alternate sources of this chunk
                    if not chunk_data:
                        print(f"Primary nodes failed for chunk {chunk.chunk_number}, looking for alternate sources...")
                        alternate_sources = find_alternate_chunk_sources(chunk.checksum, chunk.size, tried_nodes)
                        print(f"Found {len(alternate_sources)} alternate sources")
                        
                        # Try each alternate source
                        for node, alt_file_id, alt_chunk_number in alternate_sources:
                            try:
                                print(f"Trying alternate source: node {node.url}, file {alt_file_id}, chunk {alt_chunk_number}")
                                print(f"Node status: {node.status}")
                                s3_client = get_s3_client(node.url)
                                bucket_name = settings.AWS_STORAGE_BUCKET_NAME
                                chunk_key = f'{alt_file_id}/{alt_chunk_number}'
                                
                                # Try all potential bucket names with this node
                                bucket_found = False
                                for potential_bucket in get_storage_bucket_names():
                                    try:
                                        print(f"Checking if object exists: bucket={potential_bucket}, key={chunk_key}")
                                        s3_client.head_object(Bucket=potential_bucket, Key=chunk_key)
                                        print(f"✓ Object found in bucket: {potential_bucket}")
                                        bucket_name = potential_bucket
                                        bucket_found = True
                                        break
                                    except Exception as e:
                                        print(f"Object not found in bucket {potential_bucket}: {str(e)}")
                                
                                # If we couldn't find the object in any bucket, skip this node
                                if not bucket_found:
                                    print(f"Could not find chunk in any bucket on node {node.url}")
                                    continue
                                
                                # Download chunk from alternate source
                                chunk_buffer = BytesIO()
                                print(f"Downloading from bucket={bucket_name}, key={chunk_key}")
                                s3_client.download_fileobj(
                                    bucket_name, 
                                    chunk_key, 
                                    chunk_buffer
                                )
                                chunk_buffer.seek(0)
                                chunk_data = chunk_buffer.read()
                                
                                # Verify integrity with checksum
                                if hashlib.sha256(chunk_data).hexdigest() == chunk.checksum:
                                    print(f"Successfully downloaded chunk from alternate source")
                                    
                                    # If the node was marked as offline but we successfully downloaded from it,
                                    # update its status to online
                                    if node.status == 'offline':
                                        print(f"Node {node.url} was marked as offline but is actually accessible. Updating status to online.")
                                        node.status = 'online'
                                        node.consecutive_failures = 0
                                        node.recovered_at = timezone.now()
                                        node.save()
                                        print(f"Updated node {node.url} status to online")
                                    
                                    break
                            except Exception as e:
                                error = str(e)
                                print(f"Error downloading from alternate source: {error}")
                                continue
                    
                    if not chunk_data:
                        return Response({'error': f'Failed to retrieve chunk {chunk.chunk_number} from any source'}, 
                                      status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    
                    # Add to file buffer
                    file_buffer.write(chunk_data)
                
                # Cache the complete file
                file_buffer.seek(0)
                file_data = file_buffer.read()
                cache_success = cache_file(file_id, file_data)
                
                if cache_success:
                    return Response({'status': 'success', 'message': 'File cached successfully'})
                else:
                    return Response({'error': 'Failed to cache file'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
            except File.DoesNotExist:
                return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
            
        elif operation == 'remove_from_cache':
            # Remove a file from cache
            file_id = request.data.get('file_id')
            if not file_id:
                return Response({'error': 'No file_id provided'}, status=status.HTTP_400_BAD_REQUEST)
                
            import os
            cache_path = get_cache_path(file_id)
            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                    return Response({'status': 'success', 'message': 'File removed from cache'})
                except Exception as e:
                    return Response({'error': f'Failed to remove file: {str(e)}'}, 
                                  status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                return Response({'status': 'success', 'message': 'File not in cache'})
                
        else:
            return Response({'error': 'Invalid operation'}, status=status.HTTP_400_BAD_REQUEST)
