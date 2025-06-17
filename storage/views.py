"""
Views for the storage app.

This module is kept for backward compatibility.
All views have been moved to the views/ directory.
"""

# Import all views from the views package
from .views import (
    UploadView, DownloadView, NodeStatusView, FileListView, CacheManagementView,
    HealthCheckView, TaskStatusView, file_upload_view, file_list_view, file_detail_view,
    dashboard
)

# Re-export all views
__all__ = [
    'UploadView',
    'DownloadView',
    'NodeStatusView',
    'FileListView',
    'CacheManagementView',
    'HealthCheckView',
    'TaskStatusView',
    'file_upload_view',
    'file_list_view',
    'file_detail_view',
    'dashboard',
]
