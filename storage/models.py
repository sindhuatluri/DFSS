from django.db import models
from django.contrib.auth.models import User

class Node(models.Model):
    url = models.URLField(unique=True)  # e.g., 'http://minio1:9000'
    status = models.CharField(max_length=10, choices=[('online', 'Online'), ('offline', 'Offline')], default='online')
    load = models.IntegerField(default=0)  # Number of active connections or similar metric
    storage_usage = models.BigIntegerField(default=0)  # Bytes used
    
    # Health check fields
    last_check = models.DateTimeField(null=True, blank=True)
    last_latency = models.FloatField(null=True, blank=True)  # Response time in seconds
    consecutive_failures = models.IntegerField(default=0)
    failed_at = models.DateTimeField(null=True, blank=True)
    recovered_at = models.DateTimeField(null=True, blank=True)
    
    # Storage metrics
    max_capacity = models.BigIntegerField(default=1099511627776)  # Default 1TB in bytes
    
    @property
    def capacity_used_percent(self):
        """Return the percentage of storage used"""
        if self.max_capacity == 0:
            return 100.0
        return (self.storage_usage / self.max_capacity) * 100.0
    
    @property
    def health_status(self):
        """More detailed health status than just online/offline"""
        if self.status == 'offline':
            return 'offline'
        elif self.last_latency and self.last_latency > 1.0:
            return 'degraded'
        else:
            return 'healthy'
    
    @property
    def uptime(self):
        """Return uptime since last recovery or creation"""
        if self.recovered_at:
            start_time = self.recovered_at
        elif self.last_check and self.status == 'online':
            # If never failed, use last check time
            start_time = self.last_check
        else:
            return None
            
        from django.utils import timezone
        now = timezone.now()
        delta = now - start_time
        return delta.total_seconds()

    def __str__(self):
        return f"{self.url} ({self.status})"

class File(models.Model):
    name = models.CharField(max_length=255)
    size = models.BigIntegerField()
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    upload_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.size} bytes)"

class Chunk(models.Model):
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='chunks')
    chunk_number = models.IntegerField()
    checksum = models.CharField(max_length=64)  # SHA256 hash
    size = models.IntegerField()  # Size of the chunk in bytes
    nodes = models.ManyToManyField(Node, related_name='chunks')

    class Meta:
        unique_together = ('file', 'chunk_number')

    def __str__(self):
        return f"{self.file.name} - Chunk {self.chunk_number}"
