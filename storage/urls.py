from django.urls import path
from .views import (
    UploadView, DownloadView, NodeStatusView, FileListView, HealthCheckView, CacheManagementView,
    TaskStatusView, file_upload_view, file_list_view, file_detail_view, dashboard
)

# API endpoints
urlpatterns = [
    path('upload/', UploadView.as_view(), name='upload'),
    path('download/<int:file_id>/', DownloadView.as_view(), name='download'),
    path('nodes/', NodeStatusView.as_view(), name='node_status'),
    path('nodes/<int:node_id>/', NodeStatusView.as_view(), name='update_node'),
    path('files/', FileListView.as_view(), name='file_list'),
    path('files/<int:file_id>/', FileListView.as_view(), name='api_file_detail'),
    path('health/', HealthCheckView.as_view(), name='health_check'),
    path('cache/', CacheManagementView.as_view(), name='cache_management'),
    path('tasks/', TaskStatusView.as_view(), name='task_status'),
    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task_detail'),
]

# Web UI routes
urlpatterns += [
    path('web/upload/', file_upload_view, name='file_upload'),
    path('web/files/', file_list_view, name='file_list_view'),
    path('web/files/<int:file_id>/', file_detail_view, name='file_detail'),
    path('web/dashboard/', dashboard, name='web_dashboard'),
]
