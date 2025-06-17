from django.core.management.base import BaseCommand
from django.db.models import Count
from storage.models import File, Chunk, Node
from storage.utils import get_s3_client
from django.conf import settings
from io import BytesIO
import hashlib

class Command(BaseCommand):
    help = 'Optimize file distribution across storage nodes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--min-replicas', 
            type=int, 
            default=2,
            help='Minimum number of replicas for each chunk'
        )
        
        parser.add_argument(
            '--balance-load', 
            action='store_true',
            help='Balance the load across nodes'
        )
        
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes'
        )

    def handle(self, *args, **options):
        min_replicas = options['min_replicas']
        balance_load = options['balance_load']
        dry_run = options['dry_run']
        
        self.stdout.write(f"Starting storage optimization (min_replicas={min_replicas}, balance_load={balance_load}, dry_run={dry_run})")
        
        # Check for online nodes
        online_nodes = list(Node.objects.filter(status='online'))
        if not online_nodes:
            self.stdout.write(self.style.ERROR("No online nodes available"))
            return
            
        self.stdout.write(f"Found {len(online_nodes)} online nodes")
        
        # 1. Find chunks with too few replicas
        self.stdout.write("Looking for chunks with insufficient replicas...")
        
        chunks_needing_replicas = []
        for chunk in Chunk.objects.annotate(replica_count=Count('nodes')).filter(replica_count__lt=min_replicas):
            # Only include chunks that have at least one online node
            if chunk.nodes.filter(status='online').exists():
                chunks_needing_replicas.append(chunk)
        
        self.stdout.write(f"Found {len(chunks_needing_replicas)} chunks with fewer than {min_replicas} replicas")
        
        # Process chunks needing more replicas
        for chunk in chunks_needing_replicas:
            self.stdout.write(f"Optimizing chunk {chunk.id} of file '{chunk.file.name}' (chunk {chunk.chunk_number})")
            
            # Get current nodes
            current_nodes = list(chunk.nodes.filter(status='online'))
            if not current_nodes:
                self.stdout.write(self.style.WARNING(f"  Chunk {chunk.id} has no online nodes - skipping"))
                continue
                
            self.stdout.write(f"  Current replicas: {len(current_nodes)}")
            
            # Get nodes that don't have this chunk
            potential_nodes = [n for n in online_nodes if n not in current_nodes]
            
            # Sort by load (ascending)
            potential_nodes.sort(key=lambda x: x.load)
            
            # How many nodes do we need to add?
            nodes_to_add = min(min_replicas - len(current_nodes), len(potential_nodes))
            
            if nodes_to_add <= 0:
                self.stdout.write(self.style.WARNING(f"  Can't add more replicas - no available nodes"))
                continue
                
            selected_nodes = potential_nodes[:nodes_to_add]
            self.stdout.write(f"  Adding chunk to {nodes_to_add} node(s): {', '.join(n.url for n in selected_nodes)}")
            
            if dry_run:
                self.stdout.write(self.style.SUCCESS(f"  [DRY RUN] Would add chunk to {nodes_to_add} node(s)"))
                continue
            
            # Download the chunk data from an existing node
            successful = False
            chunk_data = None
            
            for source_node in current_nodes:
                try:
                    self.stdout.write(f"  Fetching chunk from node {source_node.url}")
                    s3_client = get_s3_client(source_node.url)
                    chunk_key = f'{chunk.file.id}/{chunk.chunk_number}'
                    
                    # Download chunk
                    chunk_buffer = BytesIO()
                    s3_client.download_fileobj(
                        settings.AWS_STORAGE_BUCKET_NAME, 
                        chunk_key, 
                        chunk_buffer
                    )
                    chunk_buffer.seek(0)
                    chunk_data = chunk_buffer.read()
                    
                    # Verify integrity with checksum
                    downloaded_checksum = hashlib.sha256(chunk_data).hexdigest()
                    if downloaded_checksum != chunk.checksum:
                        self.stdout.write(self.style.WARNING(f"  Checksum mismatch for chunk from {source_node.url}"))
                        continue
                    
                    successful = True
                    break
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  Error downloading from {source_node.url}: {str(e)}"))
                    continue
            
            if not successful:
                self.stdout.write(self.style.ERROR(f"  Failed to retrieve chunk data from any node"))
                continue
                
            # Upload to new nodes
            for target_node in selected_nodes:
                try:
                    self.stdout.write(f"  Uploading chunk to node {target_node.url}")
                    s3_client = get_s3_client(target_node.url)
                    chunk_key = f'{chunk.file.id}/{chunk.chunk_number}'
                    
                    # Create bucket if needed
                    try:
                        s3_client.head_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME)
                    except:
                        self.stdout.write(f"  Creating bucket on {target_node.url}")
                        s3_client.create_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME)
                    
                    # Upload the chunk
                    s3_client.put_object(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                        Key=chunk_key,
                        Body=chunk_data
                    )
                    
                    # Add the node to the chunk's nodes
                    chunk.nodes.add(target_node)
                    
                    # Update node stats
                    target_node.storage_usage += chunk.size
                    target_node.load += 1
                    target_node.save()
                    
                    self.stdout.write(self.style.SUCCESS(f"  Successfully uploaded to {target_node.url}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  Error uploading to {target_node.url}: {str(e)}"))
        
        # 2. Balance load across nodes if requested
        if balance_load:
            self.stdout.write("\nBalancing load across nodes...")
            
            # Calculate average load
            total_load = sum(n.load for n in online_nodes)
            avg_load = total_load / len(online_nodes) if online_nodes else 0
            
            self.stdout.write(f"Average node load: {avg_load:.2f}")
            
            # Find overloaded and underloaded nodes
            overloaded = [n for n in online_nodes if n.load > avg_load * 1.2]  # 20% over average
            underloaded = [n for n in online_nodes if n.load < avg_load * 0.8]  # 20% under average
            
            self.stdout.write(f"Found {len(overloaded)} overloaded and {len(underloaded)} underloaded nodes")
            
            if not (overloaded and underloaded):
                self.stdout.write("System load is already well balanced")
                return
                
            # Sort overloaded nodes by load (descending)
            overloaded.sort(key=lambda x: x.load, reverse=True)
            
            # Sort underloaded nodes by load (ascending)
            underloaded.sort(key=lambda x: x.load)
            
            # Attempt to move some chunks from overloaded to underloaded nodes
            for source_node in overloaded:
                if source_node.load <= avg_load:
                    continue  # No longer overloaded
                    
                # Find chunks stored only on this overloaded node
                self.stdout.write(f"Looking for movable chunks on {source_node.url} (load: {source_node.load})")
                
                # Get chunks that exist on this node
                chunks_to_move = []
                for chunk in source_node.chunks.all():
                    # We only want to move chunks that have multiple copies
                    if chunk.nodes.count() > 1:
                        chunks_to_move.append(chunk)
                
                self.stdout.write(f"Found {len(chunks_to_move)} candidate chunks for movement")
                
                # Sort chunks by size (smallest first)
                chunks_to_move.sort(key=lambda x: x.size)
                
                # Move chunks until node is no longer overloaded
                for chunk in chunks_to_move:
                    # Check if source node is still overloaded
                    if source_node.load <= avg_load:
                        break
                        
                    # Find an underloaded node that doesn't have this chunk
                    target_node = None
                    for node in underloaded:
                        if node.load >= avg_load:
                            continue  # No longer underloaded
                        if not chunk.nodes.filter(id=node.id).exists():
                            target_node = node
                            break
                    
                    if not target_node:
                        continue  # No suitable target node
                    
                    self.stdout.write(f"Moving chunk {chunk.id} from {source_node.url} to {target_node.url}")
                    
                    if dry_run:
                        self.stdout.write(self.style.SUCCESS(f"  [DRY RUN] Would move chunk {chunk.id}"))
                        # Simulate the effect
                        source_node.load -= 1
                        target_node.load += 1
                        continue
                    
                    # Actually move the chunk
                    try:
                        # Download from source
                        s3_client_source = get_s3_client(source_node.url)
                        chunk_key = f'{chunk.file.id}/{chunk.chunk_number}'
                        
                        chunk_buffer = BytesIO()
                        s3_client_source.download_fileobj(
                            settings.AWS_STORAGE_BUCKET_NAME, 
                            chunk_key, 
                            chunk_buffer
                        )
                        chunk_buffer.seek(0)
                        chunk_data = chunk_buffer.read()
                        
                        # Verify checksum
                        if hashlib.sha256(chunk_data).hexdigest() != chunk.checksum:
                            self.stdout.write(self.style.WARNING(f"  Checksum mismatch - skipping"))
                            continue
                        
                        # Upload to target
                        s3_client_target = get_s3_client(target_node.url)
                        s3_client_target.put_object(
                            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                            Key=chunk_key,
                            Body=chunk_data
                        )
                        
                        # Add target node to chunk's nodes
                        chunk.nodes.add(target_node)
                        
                        # Remove source node from chunk's nodes if there are other copies
                        other_nodes = chunk.nodes.exclude(id=source_node.id)
                        if other_nodes.exists():
                            chunk.nodes.remove(source_node)
                            
                            # Delete from source node storage
                            s3_client_source.delete_object(
                                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                                Key=chunk_key
                            )
                            
                            # Update node stats
                            source_node.storage_usage -= chunk.size
                            source_node.load -= 1
                            source_node.save()
                            
                            target_node.storage_usage += chunk.size
                            target_node.load += 1
                            target_node.save()
                            
                            self.stdout.write(self.style.SUCCESS(f"  Successfully moved chunk"))
                        else:
                            # Just add a copy, don't remove the original
                            self.stdout.write(f"  Adding copy instead of moving (no other copies)")
                            
                            # Update target node stats only
                            target_node.storage_usage += chunk.size
                            target_node.load += 1
                            target_node.save()
                    
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"  Error moving chunk: {str(e)}"))
        
        self.stdout.write(self.style.SUCCESS("Storage optimization completed"))