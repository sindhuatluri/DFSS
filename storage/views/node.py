"""
Node management views for the storage app.
"""

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from ..models import Node


class NodeStatusView(APIView):
    """
    API endpoint for retrieving and updating node status.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """Get status of all nodes"""
        nodes = Node.objects.all()
        data = [{
            'id': node.id,
            'url': node.url,
            'status': node.status,
            'load': node.load,
            'storage_usage': node.storage_usage,
            'health_status': node.health_status,
            'last_latency': node.last_latency,
            'last_check': node.last_check,
            'consecutive_failures': node.consecutive_failures,
            'capacity_used_percent': node.capacity_used_percent
        } for node in nodes]
        
        return Response(data)
    
    def post(self, request, node_id):
        """Update status of a specific node"""
        node = get_object_or_404(Node, id=node_id)
        
        # Check for manual control operations
        if 'operation' in request.data:
            operation = request.data['operation']
            
            if operation == 'mark_offline':
                # Import here to avoid circular imports
                from ..tasks import mark_node_as_offline
                
                task = mark_node_as_offline.delay(node_id)
                return Response({
                    'status': 'success',
                    'message': f'Node {node.url} is being marked as offline',
                    'task_id': task.id
                })
                
            elif operation == 'mark_online':
                # Import here to avoid circular imports
                from ..tasks import mark_node_as_online
                
                task = mark_node_as_online.delay(node_id)
                return Response({
                    'status': 'success',
                    'message': f'Node {node.url} is being checked and marked as online',
                    'task_id': task.id
                })
                
            elif operation == 'check_status':
                # Run an immediate check on this node
                from ..tasks import check_all_nodes_status
                
                task = check_all_nodes_status.delay()
                return Response({
                    'status': 'success',
                    'message': f'Status check initiated for all nodes',
                    'task_id': task.id
                })
                
            elif operation == 'update_metrics':
                # Update metrics for this node
                from ..tasks import update_all_nodes_metrics
                
                task = update_all_nodes_metrics.delay()
                return Response({
                    'status': 'success',
                    'message': f'Metrics update initiated for all nodes',
                    'task_id': task.id
                })
                
            elif operation == 'optimize_storage':
                # Run storage optimization
                from ..tasks import optimize_storage_distribution
                
                task = optimize_storage_distribution.delay()
                return Response({
                    'status': 'success',
                    'message': f'Storage optimization initiated',
                    'task_id': task.id
                })
                
            else:
                return Response({
                    'status': 'error',
                    'message': f'Unknown operation: {operation}'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        # Regular update operations
        if 'status' in request.data:
            node.status = request.data['status']
        
        if 'load' in request.data:
            node.load = request.data['load']
            
        if 'storage_usage' in request.data:
            node.storage_usage = request.data['storage_usage']
            
        node.save()
        
        return Response({
            'id': node.id,
            'url': node.url,
            'status': node.status,
            'load': node.load,
            'storage_usage': node.storage_usage,
            'health_status': node.health_status,
            'capacity_used_percent': node.capacity_used_percent
        })
