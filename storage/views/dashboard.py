"""
Dashboard view for the storage app.
"""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.core.cache import cache

from ..models import File, Chunk, Node
from ..utils import is_file_cached


@login_required
def dashboard(request):
    """
    Admin dashboard showing system status and statistics.
    Enhanced with node health monitoring and analytics.
    """
    # Get system stats
    file_count = File.objects.count()
    chunk_count = Chunk.objects.count()
    total_storage_used = Node.objects.aggregate(total=Sum('storage_usage'))['total'] or 0
    
    # Get node stats
    nodes = Node.objects.all()
    total_nodes_count = nodes.count()
    online_nodes_count = nodes.filter(status='online').count()
    
    # Calculate health metrics
    healthy_nodes = 0
    degraded_nodes = 0
    for node in nodes:
        # Calculate storage percentage for display
        node.storage_percentage = node.capacity_used_percent
        
        # Categorize nodes by health status
        status = node.health_status
        if status == 'healthy':
            healthy_nodes += 1
        elif status == 'degraded':
            degraded_nodes += 1
    
    # Calculate system reliability score (0-100)
    if total_nodes_count > 0:
        health_score = (healthy_nodes * 100 + degraded_nodes * 50) / total_nodes_count
    else:
        health_score = 0
    
    # System redundancy level (based on average number of nodes per chunk)
    avg_nodes_per_chunk = 0
    if chunk_count > 0:
        chunk_node_count = sum(chunk.nodes.count() for chunk in Chunk.objects.all())
        avg_nodes_per_chunk = chunk_node_count / chunk_count
    
    # Get recent files
    recent_files = File.objects.all().order_by('-upload_date')[:10]
    
    # Get files with potential issues (no chunks or single node storage)
    at_risk_files = []
    for file in File.objects.all():
        chunks = file.chunks.all()
        if not chunks.exists():
            at_risk_files.append((file, 'No chunks found'))
            continue
            
        # Check if any chunks have fewer than 2 nodes
        for chunk in chunks:
            if chunk.nodes.count() < 2:
                at_risk_files.append((file, f'Chunk {chunk.chunk_number} stored on only {chunk.nodes.count()} node(s)'))
                break
    
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
    
    # Calculate average download latency where available
    latencies = [node.last_latency for node in nodes if node.last_latency is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    
    context = {
        'file_count': file_count,
        'chunk_count': chunk_count,
        'total_storage_used': total_storage_used,
        'nodes': nodes,
        'total_nodes_count': total_nodes_count,
        'online_nodes_count': online_nodes_count,
        'healthy_nodes': healthy_nodes,
        'degraded_nodes': degraded_nodes,
        'health_score': round(health_score, 1),
        'avg_nodes_per_chunk': round(avg_nodes_per_chunk, 1),
        'recent_files': recent_files,
        'at_risk_files': at_risk_files[:5],  # Limit to 5 most at risk
        'at_risk_count': len(at_risk_files),
        'cache_hit_rate': round(cache_hit_rate, 1),
        'avg_latency': round(avg_latency * 1000, 2) if avg_latency else None,  # Convert to ms
        'system_status': 'healthy' if health_score >= 80 else 'degraded' if health_score >= 50 else 'critical',
    }
    
    return render(request, 'storage/dashboard.html', context)
