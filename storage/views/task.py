"""
Task management views for the storage app.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated


class TaskStatusView(APIView):
    """
    API endpoint for checking the status of background tasks.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, task_id=None):
        """Get status of one or all tasks"""
        from celery.result import AsyncResult
        
        if task_id:
            # Get status of a specific task
            task_result = AsyncResult(task_id)
            return Response({
                'task_id': task_id,
                'status': task_result.status,
                'result': task_result.result if task_result.ready() else None,
                'ready': task_result.ready()
            })
        else:
            # Get the last 10 tasks (this requires result backend with history)
            # This is a basic implementation and may need to be customized
            # depending on your result backend configuration
            return Response({
                'message': 'Provide a task_id to check its status',
                'example': f'{request.build_absolute_uri()}task-id-here/'
            })
