import hashlib
import time
import os
from io import BytesIO
import boto3
from django.conf import settings
from django.core.cache import cache
from .models import Node

def chunk_file(file, chunk_size=5 * 1024 * 1024):  # 5MB chunks
    """
    Split a file into chunks of specified size.
    Returns a list of tuples (BytesIO, checksum, size).
    """
    chunks = []
    while True:
        chunk_data = file.read(chunk_size)
        if not chunk_data:
            break
        
        # Calculate checksum and size
        checksum = hashlib.sha256(chunk_data).hexdigest()
        size = len(chunk_data)
        
        # Return BytesIO object for the chunk
        chunk_io = BytesIO(chunk_data)
        chunk_io.seek(0)
        
        chunks.append((chunk_io, checksum, size))
    
    # Reset file pointer for potential reuse
    file.seek(0)
    return chunks

def get_s3_client(node_url=None):
    """
    Create and return an S3 client for the specified node.
    If no node URL is provided, use the default from settings.
    """
    if node_url is None:
        node_url = settings.AWS_S3_ENDPOINT_URL
    
    try:
        import socket
        if socket.gethostname() != 'django' and 'minio' in node_url:
            # Extract port from the URL (e.g., 9000 from http://minio1:9000)
            if 'minio1:9000' in node_url:
                node_url = 'http://localhost:9000'
            elif 'minio2:9000' in node_url:
                node_url = 'http://localhost:9002'
            elif 'minio3:9000' in node_url:
                node_url = 'http://localhost:9004'
    except:
        pass
    
    return boto3.client(
        's3',
        endpoint_url=node_url,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        verify=False
    )

def get_least_loaded_nodes(n=2):
    """
    Return the n least loaded online nodes.
    """
    return Node.objects.filter(status='online').order_by('load')[:n]

def get_storage_bucket_names():
    """
    Return a list of potential bucket names to try.
    The primary bucket is first, followed by fallbacks.
    """
    primary = settings.AWS_STORAGE_BUCKET_NAME
    fallbacks = ["file-chunks", "chunks", "files", "filestore-data"]
    
    # Ensure we don't have duplicates
    all_buckets = [primary]
    for fallback in fallbacks:
        if fallback != primary:
            all_buckets.append(fallback)
    
    return all_buckets

def create_bucket_if_not_exists(s3_client, bucket_name=None):
    """
    Create the storage bucket if it doesn't exist.
    """
    if bucket_name is None:
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
    
    print(f"\n--- BUCKET CREATION/VERIFICATION PROCESS ---")
    print(f"Checking bucket: {bucket_name}")
    
    try:
        # Get endpoint from client for logging
        endpoint = s3_client._endpoint.host
        print(f"S3 client endpoint: {endpoint}")
        
        # Check bucket access policy
        print("Setting up bucket access policy...")
        try:
            import json
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": ["*"]},
                        "Action": ["s3:*"],
                        "Resource": [
                            f"arn:aws:s3:::{bucket_name}",
                            f"arn:aws:s3:::{bucket_name}/*"
                        ]
                    }
                ]
            }
            print(f"Policy to apply: {json.dumps(policy)}")
        except Exception as policy_error:
            print(f"Error preparing policy: {str(policy_error)}")
        
        # Check if bucket exists
        try:
            print("Checking if bucket exists with head_bucket...")
            s3_client.head_bucket(Bucket=bucket_name)
            print(f"Bucket {bucket_name} already exists (head_bucket succeeded)")
            bucket_exists = True
        except Exception as e:
            print(f"head_bucket failed: {str(e)}")
            print(f"Error type: {type(e).__name__}")
            if hasattr(e, 'response'):
                status_code = e.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
                print(f"HTTP status code: {status_code}")
                if status_code == 404:
                    bucket_exists = False
                    print("Bucket does not exist (404 response)")
                else:
                    print(f"Unexpected error code: {status_code}")
                    bucket_exists = None  # Unknown state
            else:
                print("No response attribute in exception")
                bucket_exists = None  # Unknown state
            
            if not bucket_exists:
                print(f"Bucket {bucket_name} doesn't exist, creating it")
                # Create the bucket - different parameters for MinIO vs AWS
                try:
                    print("Attempt 1: Creating bucket without location constraint (MinIO style)")
                    s3_client.create_bucket(Bucket=bucket_name)
                    print("Bucket creation succeeded!")
                    bucket_exists = True
                except Exception as create_error:
                    print(f"First bucket creation attempt failed: {str(create_error)}")
                    print(f"Error type: {type(create_error).__name__}")
                    if hasattr(create_error, 'response'):
                        print(f"HTTP status: {create_error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
                    
                    # Fallback to AWS-style with location constraint
                    try:
                        print("Attempt 2: Creating bucket with location constraint (AWS style)")
                        s3_client.create_bucket(
                            Bucket=bucket_name,
                            CreateBucketConfiguration={'LocationConstraint': 'us-east-1'}
                        )
                        print("Second bucket creation attempt succeeded!")
                        bucket_exists = True
                    except Exception as aws_error:
                        print(f"Second bucket creation attempt failed: {str(aws_error)}")
                        print(f"Error type: {type(aws_error).__name__}")
                        if hasattr(aws_error, 'response'):
                            print(f"HTTP status: {aws_error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
                        # Last resort - the bucket might actually exist despite head_bucket failing
                        bucket_exists = None  # Unknown state
        
        # Verify bucket exists by listing them
        try:
            print("Verifying bucket existence by listing all buckets...")
            response = s3_client.list_buckets()
            buckets = [bucket['Name'] for bucket in response['Buckets']]
            print(f"Available buckets: {buckets}")
            
            if bucket_name in buckets:
                print(f"✅ Bucket {bucket_name} found in bucket list")
                bucket_exists = True
            else:
                print(f"❌ WARNING: Bucket {bucket_name} NOT found in bucket list")
                bucket_exists = False
        except Exception as list_error:
            print(f"Error listing buckets: {str(list_error)}")
            print(f"Error type: {type(list_error).__name__}")
        
        # Apply bucket policy if bucket exists
        if bucket_exists:
            try:
                print(f"Setting bucket policy for {bucket_name}...")
                s3_client.put_bucket_policy(
                    Bucket=bucket_name,
                    Policy=json.dumps(policy)
                )
                print("✅ Bucket policy applied successfully")
            except Exception as policy_error:
                print(f"Error setting bucket policy: {str(policy_error)}")
                print(f"Error type: {type(policy_error).__name__}")
                if hasattr(policy_error, 'response'):
                    print(f"HTTP status: {policy_error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
        
        # Test by trying to put a small object
        try:
            print("Testing bucket by putting a test object...")
            test_content = b"test content"
            s3_client.put_object(
                Bucket=bucket_name,
                Key="test_key",
                Body=test_content
            )
            print("✅ Successfully put test object to bucket")
            
            # Try to get it back
            response = s3_client.get_object(
                Bucket=bucket_name,
                Key="test_key"
            )
            content = response['Body'].read()
            print(f"Retrieved test content: {content}")
            if content == test_content:
                print("✅ Test content retrieved successfully and matches")
            else:
                print(f"❌ Test content mismatch: {content} vs {test_content}")
                
            # Clean up
            s3_client.delete_object(
                Bucket=bucket_name,
                Key="test_key"
            )
            print("Test object deleted")
            
        except Exception as test_error:
            print(f"Error testing bucket with object: {str(test_error)}")
            print(f"Error type: {type(test_error).__name__}")
            if hasattr(test_error, 'response'):
                print(f"HTTP status: {test_error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
            
    except Exception as e:
        print(f"Error in create_bucket_if_not_exists: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        # Get traceback for more details
        import traceback
        print("Traceback:")
        traceback.print_exc()
    
    print("--- BUCKET CREATION/VERIFICATION COMPLETED ---\n")
    # Return the bucket name anyway and let the caller handle any further issues
    return bucket_name

# Cache directory for frequently accessed files
CACHE_DIR = os.path.join(settings.BASE_DIR, 'file_cache')

# Create cache directory if it doesn't exist
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR, exist_ok=True)

def get_cache_path(file_id):
    """Get the cache file path for a given file ID"""
    return os.path.join(CACHE_DIR, f"file_{file_id}")

def is_file_cached(file_id):
    """Check if a file is in the local cache"""
    cache_path = get_cache_path(file_id)
    return os.path.exists(cache_path)

def cache_file(file_id, file_data):
    """Store a file in the local cache"""
    try:
        cache_path = get_cache_path(file_id)
        with open(cache_path, 'wb') as f:
            f.write(file_data)
        
        # Also store access metadata in Django's cache
        cache.set(f"file_access_count_{file_id}", 1, 60*60*24*7)  # 1 week TTL
        cache.set(f"file_last_access_{file_id}", time.time(), 60*60*24*7)
        return True
    except Exception as e:
        print(f"Error caching file {file_id}: {str(e)}")
        return False

def get_cached_file(file_id):
    """Read a file from the local cache"""
    if not is_file_cached(file_id):
        return None
    
    try:
        cache_path = get_cache_path(file_id)
        with open(cache_path, 'rb') as f:
            data = f.read()
        
        # Update access metadata
        current_count = cache.get(f"file_access_count_{file_id}", 0)
        cache.set(f"file_access_count_{file_id}", current_count + 1, 60*60*24*7)
        cache.set(f"file_last_access_{file_id}", time.time(), 60*60*24*7)
        
        return data
    except Exception as e:
        print(f"Error reading cached file {file_id}: {str(e)}")
        return None

def should_cache_file(file_obj):
    """Determine if a file should be cached based on access patterns and size"""
    # Files under 100MB are candidates for caching
    if file_obj.size > 100 * 1024 * 1024:
        return False
    
    # Check access patterns
    access_count = cache.get(f"file_access_count_{file_obj.id}", 0)
    last_access = cache.get(f"file_last_access_{file_obj.id}", 0)
    
    # Cache if accessed at least 3 times or accessed in the last 24 hours
    if access_count >= 3 or (time.time() - last_access < 60*60*24):
        return True
    
    return False

def get_nearest_node(nodes):
    """
    Get the nearest node based on latency or other metrics
    In this implementation, we use a simple ping-like approach
    """
    if not nodes:
        return None
    
    fastest_node = None
    lowest_latency = float('inf')
    
    for node in nodes:
        try:
            start_time = time.time()
            s3_client = get_s3_client(node.url)
            s3_client.list_buckets()
            latency = time.time() - start_time
            
            if latency < lowest_latency:
                lowest_latency = latency
                fastest_node = node
        except Exception:
            # Skip nodes that fail connectivity check
            continue
    
    return fastest_node or nodes[0]  # Fallback to first node if all checks fail

def find_alternate_chunk_sources(checksum, size, exclude_nodes=None):
    """
    Find all nodes that might have a chunk with the same checksum and size,
    excluding any nodes in the exclude_nodes list.
    
    Returns a list of (node, file_id, chunk_number) tuples where the chunk might be found.
    First prioritizes online nodes, then includes offline nodes as fallback.
    """
    from .models import Chunk, Node
    
    # Find all chunks with this checksum and size
    matching_chunks = Chunk.objects.filter(checksum=checksum, size=size)
    
    # Prepare the exclude set
    exclude_set = set()
    if exclude_nodes:
        exclude_set = {node.id for node in exclude_nodes}
    
    # Collect all potential sources (node, file_id, chunk_number)
    online_sources = []
    offline_sources = []
    
    for chunk in matching_chunks:
        # First collect online nodes that aren't in the exclude list
        for node in chunk.nodes.filter(status='online'):
            if node.id not in exclude_set:
                online_sources.append((node, chunk.file.id, chunk.chunk_number))
        
        # Then collect offline nodes as fallback
        for node in chunk.nodes.filter(status='offline'):
            if node.id not in exclude_set:
                offline_sources.append((node, chunk.file.id, chunk.chunk_number))
    
    # Return online sources first, then offline sources as fallback
    return online_sources + offline_sources
