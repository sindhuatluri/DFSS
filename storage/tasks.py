from celery import shared_task
from django.utils import timezone
from django.db.models import Count, Sum
from django.core.management import call_command
from io import StringIO
import logging
import time

from .models import Node, File, Chunk
from .utils import get_s3_client

logger = logging.getLogger(__name__)

@shared_task
def check_all_nodes_status():
    """
    Celery task to check the health of all nodes.
    This task runs the check_node_health management command with immediate flag.
    """
    logger.info("Starting node health check task")
    output = StringIO()
    
    try:
        # Run the management command with immediate flag
        call_command('check_node_health', '--immediate', stdout=output)
        result = output.getvalue()
        logger.info(f"Node health check completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in node health check task: {str(e)}")
        return f"Error: {str(e)}"
    
@shared_task
def update_all_nodes_metrics():
    """
    Celery task to update metrics for all nodes.
    This task collects:
    - Storage usage
    - Load metrics
    - Performance metrics
    """
    logger.info("Starting node metrics update task")
    
    # Get all online nodes
    online_nodes = Node.objects.filter(status='online')
    logger.info(f"Found {online_nodes.count()} online nodes to update metrics for")
    
    for node in online_nodes:
        try:
            # Measure latency
            s3_client = get_s3_client(node.url)
            start_time = time.time()
            s3_client.list_buckets()
            latency = time.time() - start_time
            
            # Update load and storage stats
            node_chunks = node.chunks.count()
            total_storage = node.chunks.aggregate(total=Sum('size'))['total'] or 0
            
            # Update node metrics
            node.last_latency = latency
            node.last_check = timezone.now()
            node.load = node_chunks
            node.storage_usage = total_storage
            node.save()
            
            logger.info(f"Updated metrics for {node.url}: latency={latency:.4f}s, storage={total_storage}, load={node_chunks}")
        except Exception as e:
            logger.error(f"Error updating metrics for node {node.url}: {str(e)}")
    
    return f"Updated metrics for {online_nodes.count()} nodes"

@shared_task
def optimize_storage_distribution():
    """
    Celery task to optimize storage distribution across nodes.
    This task runs the optimize_storage management command.
    """
    logger.info("Starting storage optimization task")
    output = StringIO()
    
    try:
        # Run the management command with defaults
        call_command('optimize_storage', '--min-replicas=2', '--balance-load', stdout=output)
        result = output.getvalue()
        logger.info(f"Storage optimization completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error in storage optimization task: {str(e)}")
        return f"Error: {str(e)}"

@shared_task
def cleanup_offline_nodes():
    """
    Celery task to clean up nodes that have been offline for a long time.
    This task will move data from long-term offline nodes to healthy nodes.
    """
    logger.info("Starting offline nodes cleanup task")
    
    # Find nodes that have been offline for more than 24 hours
    one_day_ago = timezone.now() - timezone.timedelta(days=1)
    long_term_offline_nodes = Node.objects.filter(
        status='offline', 
        failed_at__lt=one_day_ago
    )
    
    logger.info(f"Found {long_term_offline_nodes.count()} long-term offline nodes")
    
    if not long_term_offline_nodes.exists():
        return "No long-term offline nodes to clean up"
    
    # For each offline node, ensure its chunks have enough replicas
    for node in long_term_offline_nodes:
        # Find chunks that are on this node
        chunks = node.chunks.all()
        logger.info(f"Node {node.url} has {chunks.count()} chunks")
        
        # For each chunk, check if it has enough replicas on online nodes
        for chunk in chunks:
            online_replicas = chunk.nodes.filter(status='online').count()
            if online_replicas < 2:
                logger.info(f"Chunk {chunk.id} has only {online_replicas} online replicas - running optimization")
                # Run storage optimization specifically for this chunk
                try:
                    # We can't easily run the command for a specific chunk, so we run the full optimization
                    output = StringIO()
                    call_command('optimize_storage', '--min-replicas=2', stdout=output)
                    logger.info(f"Optimization for chunk {chunk.id} completed")
                    break  # Only need to run optimization once per node
                except Exception as e:
                    logger.error(f"Error optimizing storage for node {node.url}: {str(e)}")
    
    return f"Cleaned up {long_term_offline_nodes.count()} long-term offline nodes"

@shared_task
def mark_node_as_offline(node_id):
    """
    Manually mark a node as offline and trigger replication of its chunks.
    """
    try:
        node = Node.objects.get(id=node_id)
        logger.info(f"Manually marking node {node.url} as offline")
        
        node.status = 'offline'
        node.failed_at = timezone.now()
        node.consecutive_failures = 999  # High number to prevent auto-recovery
        node.save()
        
        # Run optimization to ensure chunks on this node are properly replicated
        optimize_storage_distribution.delay()
        
        return f"Node {node.url} marked as offline and optimization scheduled"
    except Node.DoesNotExist:
        return f"Node with ID {node_id} not found"
    except Exception as e:
        logger.error(f"Error marking node {node_id} as offline: {str(e)}")
        return f"Error: {str(e)}"

@shared_task
def mark_node_as_online(node_id):
    """
    Manually mark a node as online after maintenance or recovery.
    """
    try:
        node = Node.objects.get(id=node_id)
        logger.info(f"Manually marking node {node.url} as online")
        
        # Check if the node is actually reachable
        try:
            s3_client = get_s3_client(node.url)
            s3_client.list_buckets()  # Test connection
            
            node.status = 'online'
            node.recovered_at = timezone.now()
            node.consecutive_failures = 0
            node.save()
            
            return f"Node {node.url} verified and marked as online"
        except Exception as e:
            return f"Failed to connect to node {node.url}: {str(e)}"
    
    except Node.DoesNotExist:
        return f"Node with ID {node_id} not found"
    except Exception as e:
        logger.error(f"Error marking node {node_id} as online: {str(e)}")
        return f"Error: {str(e)}"