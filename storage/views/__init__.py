"""
Views for the storage app.
This module exposes all views from the individual view modules.
"""

from .upload import UploadView
from .download import DownloadView
from .node import NodeStatusView
from .file import FileListView, file_upload_view, file_list_view, file_detail_view
from .cache import CacheManagementView
from .health import HealthCheckView
from .task import TaskStatusView
from .dashboard import dashboard

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
