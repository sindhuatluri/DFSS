import os
from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'filestore.settings')

app = Celery('filestore')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Define periodic tasks
app.conf.beat_schedule = {
    'check-node-status-every-1-seconds': {
        'task': 'storage.tasks.check_all_nodes_status',
        'schedule': 1,  # seconds
    },
    'update-node-metrics-every-15-minutes': {
        'task': 'storage.tasks.update_all_nodes_metrics',
        'schedule': 900.0,  # seconds
    },
    'cleanup-offline-nodes-daily': {
        'task': 'storage.tasks.cleanup_offline_nodes',
        'schedule': crontab(hour=1, minute=0),  # Run at 1:00 AM every day
    },
}
