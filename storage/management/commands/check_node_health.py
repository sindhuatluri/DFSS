from django.core.management.base import BaseCommand
from django.utils import timezone
from storage.models import Node
from storage.utils import get_s3_client
import time
import datetime

class Command(BaseCommand):
    help = 'Check health of MinIO nodes and update their status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold', 
            type=int, 
            default=1,  # Changed from 5 to 1 for immediate status change
            help='Number of consecutive failures before marking a node as offline'
        )
        
        parser.add_argument(
            '--auto-recover', 
            action='store_true',
            help='Automatically try to recover offline nodes'
        )
        
        parser.add_argument(
            '--immediate', 
            action='store_true',
            help='Immediately mark nodes as offline on first failure (overrides threshold)'
        )

    def handle(self, *args, **options):
        threshold = options['threshold']
        auto_recover = options['auto_recover']
        immediate = options['immediate']
        
        # If immediate flag is set, override threshold to 1
        if immediate:
            threshold = 1
            self.stdout.write("Immediate mode enabled - nodes will be marked offline on first failure")
        
        self.stdout.write(f"Starting node health check with threshold {threshold}")
        
        # Get all nodes
        nodes = Node.objects.all()
        self.stdout.write(f"Found {nodes.count()} nodes")
        
        for node in nodes:
            self.stdout.write(f"Checking node {node.url} (current status: {node.status})")
            
            # Check if the node is online
            try:
                s3_client = get_s3_client(node.url)
                start_time = time.time()
                s3_client.list_buckets()
                latency = time.time() - start_time
                
                # Update node metrics
                node.last_latency = latency
                node.last_check = timezone.now()
                node.consecutive_failures = 0
                
                # If node was offline, bring it back online
                if node.status == 'offline':
                    self.stdout.write(self.style.SUCCESS(f"Node {node.url} recovered - bringing back online"))
                    node.status = 'online'
                    node.recovered_at = timezone.now()
                    
                node.save()
                self.stdout.write(f"Node {node.url} is healthy (latency: {latency:.4f}s)")
                
            except Exception as e:
                # Increment failure count
                if not node.consecutive_failures:
                    node.consecutive_failures = 1
                else:
                    node.consecutive_failures += 1
                
                node.last_check = timezone.now()
                
                # Special handling for connection failures - these are more likely to indicate actual downtime
                connection_error = False
                error_msg = str(e).lower()
                if "connect" in error_msg or "connection" in error_msg or "timeout" in error_msg or "endpoint" in error_msg:
                    connection_error = True
                
                # Mark as offline if connection error and immediate mode or threshold reached
                if ((connection_error and immediate) or 
                    node.consecutive_failures >= threshold) and node.status == 'online':
                    self.stdout.write(self.style.ERROR(
                        f"Node {node.url} is unreachable - marking offline"
                    ))
                    node.status = 'offline'
                    node.failed_at = timezone.now()
                else:
                    self.stdout.write(
                        f"Node {node.url} failed check ({node.consecutive_failures}/{threshold}): {str(e)}"
                    )
                
                node.save()
        
        # Attempt to recover offline nodes if auto-recover is enabled
        if auto_recover:
            offline_nodes = Node.objects.filter(status='offline')
            self.stdout.write(f"Found {offline_nodes.count()} offline nodes to try recovering")
            
            for node in offline_nodes:
                # Only try recovery after 15 minutes of being offline
                if not node.failed_at or (timezone.now() - node.failed_at).total_seconds() < 15*60:
                    continue
                
                self.stdout.write(f"Attempting to recover node {node.url}")
                
                try:
                    s3_client = get_s3_client(node.url)
                    s3_client.list_buckets()
                    
                    # Node is back! Update status
                    node.status = 'online'
                    node.recovered_at = timezone.now()
                    node.consecutive_failures = 0
                    node.save()
                    
                    self.stdout.write(self.style.SUCCESS(f"Node {node.url} recovered successfully"))
                except Exception as e:
                    self.stdout.write(f"Failed to recover node {node.url}: {str(e)}")
        
        # Log overall system health - Refresh the count to get the updated status
        total_nodes = nodes.count()
        
        # Query the database again to get the current online nodes after status updates
        online_nodes = Node.objects.filter(status='online').count()
        offline_nodes = Node.objects.filter(status='offline').count()
        
        # Double-check that our counts match
        if online_nodes + offline_nodes != total_nodes:
            self.stdout.write(self.style.WARNING(
                f"Node count mismatch: {online_nodes} online + {offline_nodes} offline != {total_nodes} total"
            ))
        
        health_percentage = (online_nodes / total_nodes * 100) if total_nodes > 0 else 0
        
        # Color-code the output based on health percentage
        if health_percentage >= 80:
            status_style = self.style.SUCCESS
        elif health_percentage >= 50:
            status_style = self.style.WARNING
        else:
            status_style = self.style.ERROR
            
        self.stdout.write(status_style(
            f"System health check complete: {online_nodes}/{total_nodes} nodes online ({health_percentage:.1f}%)"
        ))