"""
Download views for the storage app.
"""

from django.shortcuts import get_object_or_404
from django.http import StreamingHttpResponse
from django.core.cache import cache
from django.utils import timezone
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from io import BytesIO
import hashlib
import time
import traceback

from ..models import File, Chunk, Node
from ..utils import (
    get_s3_client,
    get_nearest_node,
    is_file_cached,
    get_cached_file,
    should_cache_file,
    find_alternate_chunk_sources,
    get_storage_bucket_names,
)
from ..tasks import optimize_storage_distribution


class DownloadView(APIView):
    """
    API endpoint for file downloads.
    Retrieves chunks from MinIO nodes and reconstructs the file.
    Features:
    - Caching for frequently accessed files
    - Nearest node selection for optimal download speed
    - Error recovery with automatic node failover
    - Robust handling of offline nodes
    - Automatic recovery attempts for missing chunks
    - Memory-efficient streaming response
    """

    permission_classes = [IsAuthenticated]

    def get_node_bucket_map(self, node):
        """
        Get or create a cached mapping of node to valid bucket names.
        This reduces redundant API calls to check bucket existence.
        """
        cache_key = f"node_bucket_map_{node.id}"
        bucket_map = cache.get(cache_key)

        if bucket_map is None:
            bucket_map = {}
            try:
                s3_client = get_s3_client(node.url)

                # List all buckets on this node to ensure we check all possibilities
                try:
                    response = s3_client.list_buckets()
                    for bucket in response["Buckets"]:
                        bucket_map[bucket["Name"]] = True
                    print(
                        f"Found {len(bucket_map)} buckets on node {node.url}: {list(bucket_map.keys())}"
                    )
                except Exception as e:
                    print(f"Error listing buckets on node {node.url}: {str(e)}")

                    # Fallback to checking expected bucket names
                    for bucket_name in get_storage_bucket_names():
                        try:
                            s3_client.head_bucket(Bucket=bucket_name)
                            bucket_map[bucket_name] = True
                            print(f"Bucket {bucket_name} exists on node {node.url}")
                        except Exception as be:
                            print(
                                f"Bucket {bucket_name} doesn't exist on node {node.url}: {str(be)}"
                            )
                            bucket_map[bucket_name] = False

                # Cache the bucket map for 15 minutes (shorter time to avoid stale data)
                cache.set(cache_key, bucket_map, 60 * 15)
            except Exception as e:
                print(f"Error creating bucket map for node {node.url}: {str(e)}")
                return {}

        return bucket_map

    def find_chunk_in_node(self, node, file_id, chunk_number, expected_checksum):
        """
        Find a chunk in a node's buckets and return the bucket name and chunk data if found.
        """
        try:
            s3_client = get_s3_client(node.url)
            chunk_key = f"{file_id}/{chunk_number}"

            # Use cached bucket map to avoid redundant API calls
            bucket_map = self.get_node_bucket_map(node)

            print(
                f"Searching for chunk {chunk_key} in {len(bucket_map)} buckets on node {node.url}"
            )

            # Try all buckets on this node
            for bucket_name, exists in bucket_map.items():
                if not exists:
                    continue

                try:
                    print(f"Checking bucket {bucket_name} for chunk {chunk_key}")
                    # Check if object exists
                    s3_client.head_object(Bucket=bucket_name, Key=chunk_key)
                    print(f"✓ Found chunk in bucket {bucket_name}")

                    # Download chunk
                    chunk_buffer = BytesIO()
                    s3_client.download_fileobj(bucket_name, chunk_key, chunk_buffer)
                    chunk_buffer.seek(0)
                    chunk_data = chunk_buffer.read()

                    # Verify integrity with checksum
                    downloaded_checksum = hashlib.sha256(chunk_data).hexdigest()
                    if downloaded_checksum != expected_checksum:
                        print(f"Checksum mismatch for chunk from node {node.url}")
                        print(f"Expected: {expected_checksum}")
                        print(f"Received: {downloaded_checksum}")
                        continue

                    return bucket_name, chunk_data
                except Exception as e:
                    print(
                        f"Error checking bucket {bucket_name} on node {node.url}: {str(e)}"
                    )
                    continue

            # If we reach here, we couldn't find the chunk in any bucket
            print(f"Chunk {chunk_key} not found in any bucket on node {node.url}")
            return None, None
        except Exception as e:
            print(f"Error accessing node {node.url}: {str(e)}")
            return None, None

    def stream_file_chunks(self, file_obj, chunks):
        """
        Generator function to stream file chunks.
        This avoids loading the entire file into memory.
        """
        # Track if we need to trigger storage optimization
        missing_chunks = []

        # Track access for cache decision
        file_id = file_obj.id
        current_count = cache.get(f"file_access_count_{file_id}", 0)
        cache.set(f"file_access_count_{file_id}", current_count + 1, 60 * 60 * 24 * 7)
        cache.set(f"file_last_access_{file_id}", time.time(), 60 * 60 * 24 * 7)

        # Should we cache this file?
        should_cache = should_cache_file(file_obj) and not is_file_cached(file_id)

        # Fetch and yield chunks
        for chunk in chunks:
            print(
                f"Processing chunk {chunk.chunk_number}, size: {chunk.size}, checksum: {chunk.checksum[:8]}..."
            )

            # Check if this chunk is already cached
            chunk_cache_key = f"file_chunk_{file_id}_{chunk.chunk_number}"
            cached_chunk = cache.get(chunk_cache_key)

            if cached_chunk:
                print(f"Chunk {chunk.chunk_number} found in cache, serving from cache")
                yield cached_chunk
                continue

            # Get available nodes for this chunk
            online_nodes = list(chunk.nodes.filter(status="online"))
            offline_nodes = list(chunk.nodes.filter(status="offline"))
            print(
                f"Found {len(online_nodes)} online and {len(offline_nodes)} offline nodes for chunk {chunk.chunk_number}"
            )

            # Try primary online nodes first if available
            if online_nodes:
                # Sort nodes by best connectivity - use nearest node first
                nearest_node = get_nearest_node(online_nodes)
                if nearest_node:
                    # Put the nearest node first in the list
                    online_nodes.remove(nearest_node)
                    online_nodes.insert(0, nearest_node)
                    print(f"Selected {nearest_node.url} as nearest node")
            else:
                print(
                    f"Warning: No online nodes available for chunk {chunk.chunk_number}"
                )

            # Try to download from each node until successful
            chunk_data = None
            error = None
            successful_node = None
            tried_nodes = list(online_nodes)  # Track nodes we've tried

            # Try online nodes first
            for node in online_nodes:
                try:
                    print(f"Attempting to download from online node: {node.url}")
                    bucket_name, chunk_data = self.find_chunk_in_node(
                        node, file_obj.id, chunk.chunk_number, chunk.checksum
                    )

                    if chunk_data:
                        print(
                            f"Successfully downloaded chunk {chunk.chunk_number} from {node.url} in bucket {bucket_name}"
                        )
                        successful_node = node
                        break
                except Exception as e:
                    error = str(e)
                    print(f"Error downloading from {node.url}: {error}")
                    continue

            # If online nodes failed, search for alternate sources of this chunk
            if chunk_data is None:
                print(
                    f"Online nodes failed for chunk {chunk.chunk_number}, looking for alternate sources..."
                )
                alternate_sources = find_alternate_chunk_sources(
                    chunk.checksum, chunk.size, tried_nodes
                )
                print(f"Found {len(alternate_sources)} potential alternate sources")

                # Try each alternate source
                for node, alt_file_id, alt_chunk_number in alternate_sources:
                    try:
                        print(
                            f"Trying alternate source: node {node.url}, file {alt_file_id}, chunk {alt_chunk_number}"
                        )
                        bucket_name, chunk_data = self.find_chunk_in_node(
                            node, alt_file_id, alt_chunk_number, chunk.checksum
                        )

                        if chunk_data:
                            print(
                                f"Successfully downloaded chunk from alternate source {node.url} in bucket {bucket_name}"
                            )
                            successful_node = node

                            # If the node was marked as offline but we successfully downloaded from it,
                            # update its status to online
                            if node.status == "offline":
                                print(
                                    f"Node {node.url} was marked as offline but is actually accessible. Updating status to online."
                                )
                                node.status = "online"
                                node.consecutive_failures = 0
                                node.recovered_at = timezone.now()
                                node.save()
                                print(f"Updated node {node.url} status to online")

                            break
                    except Exception as e:
                        error = str(e)
                        print(
                            f"Error downloading from alternate source {node.url}: {error}"
                        )
                        continue

            # If we've tried all online nodes and alternates and still don't have valid chunk data,
            # try offline nodes as a last resort
            if chunk_data is None and offline_nodes:
                print(
                    f"All online sources failed for chunk {chunk.chunk_number}, trying offline nodes as last resort..."
                )

                for node in offline_nodes:
                    try:
                        print(f"Attempting to download from offline node: {node.url}")
                        bucket_name, chunk_data = self.find_chunk_in_node(
                            node, file_obj.id, chunk.chunk_number, chunk.checksum
                        )

                        if chunk_data:
                            print(
                                f"Successfully downloaded chunk from offline node {node.url} in bucket {bucket_name}"
                            )
                            successful_node = node

                            # Update node status since it's actually accessible
                            print(
                                f"Node {node.url} was marked as offline but is actually accessible. Updating status to online."
                            )
                            node.status = "online"
                            node.consecutive_failures = 0
                            node.recovered_at = timezone.now()
                            node.save()
                            print(f"Updated node {node.url} status to online")
                            break
                    except Exception as e:
                        error = str(e)
                        print(
                            f"Error downloading from offline node {node.url}: {error}"
                        )
                        continue

            # If we've tried all nodes and still don't have valid chunk data
            if chunk_data is None:
                # Add to missing chunks list for later optimization
                missing_chunks.append(chunk.chunk_number)

                err_msg = f"Failed to retrieve chunk {chunk.chunk_number} from any source: {error}"
                print(f"Error: {err_msg}")

                # Trigger storage optimization to try to recover the chunk
                print(
                    f"Triggering storage optimization to attempt recovery of missing chunk {chunk.chunk_number}"
                )
                try:
                    optimize_task = optimize_storage_distribution.delay()
                    print(f"Storage optimization task started: {optimize_task.id}")
                except Exception as opt_error:
                    print(f"Failed to trigger storage optimization: {str(opt_error)}")

                # We can't continue streaming if a chunk is missing
                raise Exception(err_msg)

            # Cache the chunk individually if needed
            if should_cache:
                cache.set(
                    chunk_cache_key, chunk_data, 60 * 60 * 24
                )  # Cache for 24 hours

            # Update node load stats
            if successful_node:
                successful_node.load += 1
                successful_node.save()
                print(
                    f"Updated load for node {successful_node.url}: {successful_node.load}"
                )

            # Yield the chunk data for streaming
            yield chunk_data

    def get(self, request, file_id):
        print(f"\n=== DOWNLOAD PROCESS STARTED FOR FILE {file_id} ===")
        try:
            file_obj = get_object_or_404(File, id=file_id, owner=request.user)
            print(f"File found: {file_obj.name}, size: {file_obj.size}")

            # Check if file is in cache
            if is_file_cached(file_id):
                print(f"File {file_id} found in cache, serving from cache")
                cached_data = get_cached_file(file_id)
                if cached_data:
                    response = StreamingHttpResponse(
                        (chunk for chunk in [cached_data]),
                        content_type="application/octet-stream",
                    )
                    response["Content-Disposition"] = (
                        f'attachment; filename="{file_obj.name}"'
                    )
                    print(f"File {file_id} served from cache successfully")
                    print("=== DOWNLOAD PROCESS COMPLETED (CACHED) ===\n")
                    return response
                else:
                    print(
                        f"Failed to read file {file_id} from cache, falling back to nodes"
                    )

            # Get chunks
            chunks = file_obj.chunks.all().order_by("chunk_number")
            if not chunks:
                print(f"Error: File {file_id} has no chunks")
                return Response(
                    {"error": "File has no chunks"}, status=status.HTTP_404_NOT_FOUND
                )

            print(f"File has {chunks.count()} chunks")

            # Create a streaming response
            response = StreamingHttpResponse(
                self.stream_file_chunks(file_obj, chunks),
                content_type="application/octet-stream",
            )
            response["Content-Disposition"] = f'attachment; filename="{file_obj.name}"'
            response["Content-Length"] = file_obj.size

            print(f"File {file_id} successfully served")
            print("=== DOWNLOAD PROCESS COMPLETED ===\n")
            return response

        except Exception as e:
            # Global exception handler
            print(f"ERROR in download process: {str(e)}")
            print(f"Error type: {type(e).__name__}")

            # Get traceback for more details
            print("Traceback:")
            traceback.print_exc()

            print("=== DOWNLOAD PROCESS FAILED ===\n")
            return Response(
                {
                    "error": f"Download failed: {str(e)}",
                    "missing_chunks": getattr(e, "missing_chunks", []),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
