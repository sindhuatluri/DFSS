"""
Health check views for the storage app.
"""

from django.db.models import Sum
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ..models import File, Chunk, Node


class HealthCheckView(APIView):
    """
    API endpoint for system health check.
    """
    def get(self, request):
        # Count nodes and their status
        nodes = Node.objects.all()
        total_nodes = nodes.count()
        online_nodes = nodes.filter(status='online').count()
        
        # Get more detailed health metrics
        healthy_nodes = sum(1 for node in nodes if node.health_status == 'healthy')
        degraded_nodes = sum(1 for node in nodes if node.health_status == 'degraded')
        
        # Calculate health score (0-100)
        if total_nodes > 0:
            health_score = (healthy_nodes * 100 + degraded_nodes * 50) / total_nodes
        else:
            health_score = 0
        
        # System is healthy if score is at least 80
        is_healthy = health_score >= 80
        
        # Calculate redundancy metrics
        chunk_count = Chunk.objects.count()
        avg_nodes_per_chunk = 0
        if chunk_count > 0:
            chunk_node_count = sum(chunk.nodes.count() for chunk in Chunk.objects.all())
            avg_nodes_per_chunk = chunk_node_count / chunk_count
        
        # Count at-risk files (those without proper redundancy)
        at_risk_files = 0
        for file in File.objects.all():
            for chunk in file.chunks.all():
                if chunk.nodes.count() < 2:
                    at_risk_files += 1
                    break
        
        # Get storage metrics
        total_storage = nodes.aggregate(total=Sum('storage_usage'))['total'] or 0
        total_capacity = nodes.aggregate(total=Sum('max_capacity'))['total'] or 1
        storage_percent = (total_storage / total_capacity) * 100 if total_capacity > 0 else 0
        
        data = {
            'status': 'healthy' if is_healthy else 'degraded' if health_score >= 50 else 'critical',
            'health_score': round(health_score, 1),
            'total_nodes': total_nodes,
            'online_nodes': online_nodes,
            'healthy_nodes': healthy_nodes,
            'degraded_nodes': degraded_nodes,
            'storage': {
                'used': total_storage,
                'capacity': total_capacity,
                'percent_used': round(storage_percent, 1)
            },
            'redundancy': {
                'avg_nodes_per_chunk': round(avg_nodes_per_chunk, 1),
                'at_risk_files': at_risk_files
            },
            'timestamp': timezone.now().isoformat()
        }
        
        status_code = status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(data, status=status_code)
