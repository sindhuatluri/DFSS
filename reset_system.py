#!/usr/bin/env python3
"""
Reset script for the distributed file storage system.
This script deletes all files, chunks, and cached data, and resets node statistics.
USE WITH CAUTION: This will permanently delete all stored files!
"""

import os
import sys
import django
import shutil

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'filestore.settings')
django.setup()

from django.conf import settings
from storage.models import File, Chunk, Node
from storage.utils import get_s3_client, CACHE_DIR

def prompt_for_confirmation():
    """Prompt for confirmation before proceeding"""
    print("\n⚠️  WARNING: This will permanently delete all files in the system! ⚠️")
    print("All files, chunks, and cached data will be removed.")
    print("This action cannot be undone.")
    
    confirmation = input("\nType 'DELETE ALL' to confirm: ")
    
    if confirmation.strip() != "DELETE ALL":
        print("Confirmation failed. Aborting reset operation.")
        sys.exit(1)
    
    print("\nConfirmation received. Proceeding with system reset...\n")

def clear_storage():
    """Delete all objects from MinIO storage"""
    print("=== Clearing MinIO Storage ===")
    
    # Get all nodes
    nodes = Node.objects.all()
    
    if not nodes:
        print("No nodes found!")
        return
    
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME
    for node in nodes:
        try:
            print(f"Clearing objects from node {node.url}...")
            s3_client = get_s3_client(node.url)
            
            # List all objects in the bucket
            try:
                objects = []
                paginator = s3_client.get_paginator('list_objects_v2')
                
                for page in paginator.paginate(Bucket=bucket_name):
                    if 'Contents' in page:
                        objects.extend(page['Contents'])
                
                if not objects:
                    print(f"No objects found on node {node.url}")
                    continue
                
                print(f"Found {len(objects)} objects on node {node.url}")
                
                # Delete objects in batches
                for i in range(0, len(objects), 1000):
                    batch = objects[i:i+1000]
                    delete_keys = {'Objects': [{'Key': obj['Key']} for obj in batch]}
                    
                    s3_client.delete_objects(
                        Bucket=bucket_name,
                        Delete=delete_keys
                    )
                
                print(f"✓ Successfully cleared all objects from node {node.url}")
            
            except Exception as list_error:
                print(f"Error listing objects on {node.url}: {str(list_error)}")
                
                # Try alternate method - just keep the bucket
                try:
                    print(f"Attempting to recreate bucket on {node.url}...")
                    
                    # Check if bucket exists
                    try:
                        s3_client.head_bucket(Bucket=bucket_name)
                        
                        # Delete and recreate bucket
                        print(f"Deleting bucket on {node.url}...")
                        s3_client.delete_bucket(Bucket=bucket_name)
                        
                        print(f"Recreating bucket on {node.url}...")
                        s3_client.create_bucket(Bucket=bucket_name)
                        
                        print(f"✓ Successfully recreated bucket on {node.url}")
                    except:
                        # Bucket doesn't exist, just create it
                        print(f"Creating bucket on {node.url}...")
                        s3_client.create_bucket(Bucket=bucket_name)
                        print(f"✓ Successfully created bucket on {node.url}")
                        
                except Exception as bucket_error:
                    print(f"Error recreating bucket on {node.url}: {str(bucket_error)}")
        
        except Exception as e:
            print(f"Error clearing node {node.url}: {str(e)}")
    
    print("Storage clearing process completed")

def clear_database():
    """Clear all file and chunk records from the database"""
    print("\n=== Clearing Database Records ===")
    
    file_count = File.objects.count()
    chunk_count = Chunk.objects.count()
    
    print(f"Deleting {file_count} files and {chunk_count} chunks...")
    
    # This will cascade delete all chunks due to the foreign key relationship
    File.objects.all().delete()
    
    # Reset node statistics
    for node in Node.objects.all():
        node.load = 0
        node.storage_usage = 0
        node.save()
    
    print(f"✓ Successfully cleared all database records")
    print(f"✓ Reset statistics for {Node.objects.count()} nodes")

def clear_cache():
    """Clear all cached files"""
    print("\n=== Clearing File Cache ===")
    
    if os.path.exists(CACHE_DIR):
        try:
            # Count files
            cache_files = [f for f in os.listdir(CACHE_DIR) if os.path.isfile(os.path.join(CACHE_DIR, f))]
            print(f"Removing {len(cache_files)} cached files...")
            
            # Remove all files in the cache directory
            for filename in cache_files:
                os.remove(os.path.join(CACHE_DIR, filename))
            
            print(f"✓ Successfully cleared file cache")
        except Exception as e:
            print(f"Error clearing cache: {str(e)}")
    else:
        print("No cache directory found")

if __name__ == "__main__":
    print("=== Distributed Storage System Reset ===")
    
    # Skip confirmation if --force flag is provided
    if len(sys.argv) > 1 and sys.argv[1] == '--force':
        print("Force flag detected, skipping confirmation")
    else:
        prompt_for_confirmation()
    
    # Perform reset operations
    clear_storage()
    clear_database()
    clear_cache()
    
    print("\n✅ SYSTEM RESET COMPLETED")
    print("The distributed storage system has been reset to a clean state.")
    print("You can now upload new files to the system.")