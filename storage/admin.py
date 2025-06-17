from django.contrib import admin
from .models import Node, File, Chunk

@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ('url', 'status', 'load', 'storage_usage')
    list_filter = ('status',)
    search_fields = ('url',)

@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ('name', 'size', 'owner', 'upload_date')
    list_filter = ('upload_date',)
    search_fields = ('name', 'owner__username')

@admin.register(Chunk)
class ChunkAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'checksum', 'size')
    search_fields = ('file__name', 'checksum')
