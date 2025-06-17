from django.core.management.base import BaseCommand
from storage.models import Node

class Command(BaseCommand):
    help = 'Seeds MinIO nodes into the database'

    def handle(self, *args, **kwargs):
        nodes = [
            'http://minio1:9000',
            'http://minio2:9000',
            'http://minio3:9000',
        ]
        
        # When running locally without Docker, we might need to use localhost with different ports
        try:
            import socket
            if socket.gethostname() != 'django':
                nodes = [
                    'http://localhost:9000',
                    'http://localhost:9002',
                    'http://localhost:9004',
                ]
        except:
            pass
        for url in nodes:
            node, created = Node.objects.get_or_create(url=url, defaults={'status': 'online'})
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created node: {url}'))
            else:
                self.stdout.write(f'Node already exists: {url}')
        
        self.stdout.write(self.style.SUCCESS('MinIO nodes seeded successfully'))