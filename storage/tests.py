from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient
from unittest.mock import patch, MagicMock
import io
import hashlib

from .models import File, Chunk, Node
from .utils import chunk_file

class FileChunkingTest(TestCase):
    """Tests for the file chunking functionality"""
    
    def test_chunk_file(self):
        # Create a test file with known content
        test_content = b"This is a test file content" * 1000  # ~26KB
        test_file = SimpleUploadedFile("test.txt", test_content)
        
        # Use a smaller chunk size for testing
        chunk_size = 5 * 1024  # 5KB
        chunks = chunk_file(test_file, chunk_size)
        
        # Verify number of chunks
        expected_chunks = (len(test_content) + chunk_size - 1) // chunk_size
        self.assertEqual(len(chunks), expected_chunks)
        
        # Verify chunk checksums
        for i, (chunk_io, checksum, size) in enumerate(chunks):
            start = i * chunk_size
            end = min(start + chunk_size, len(test_content))
            expected_content = test_content[start:end]
            expected_checksum = hashlib.sha256(expected_content).hexdigest()
            
            self.assertEqual(checksum, expected_checksum)
            self.assertEqual(size, len(expected_content))
            
            # Check actual content
            chunk_io.seek(0)
            actual_content = chunk_io.read()
            self.assertEqual(actual_content, expected_content)

class APITests(TestCase):
    """Tests for the API endpoints"""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        
        # Create test nodes
        self.node1 = Node.objects.create(url='http://minio1:9000', status='online')
        self.node2 = Node.objects.create(url='http://minio2:9000', status='online')
    
    @patch('storage.views.get_s3_client')
    @patch('storage.views.create_bucket_if_not_exists')
    def test_upload_file(self, mock_create_bucket, mock_get_s3_client):
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_get_s3_client.return_value = mock_s3
        mock_create_bucket.return_value = 'distributed-storage'
        
        # Create a test file
        test_content = b"Test file content" * 1000
        test_file = SimpleUploadedFile("test.txt", test_content)
        
        # Upload the file
        response = self.client.post(
            reverse('upload'),
            {'file': test_file},
            format='multipart'
        )
        
        # Check response
        self.assertEqual(response.status_code, 201)
        self.assertIn('id', response.data)
        self.assertEqual(response.data['name'], 'test.txt')
        
        # Check database
        file_id = response.data['id']
        file_obj = File.objects.get(id=file_id)
        self.assertEqual(file_obj.owner, self.user)
        self.assertEqual(file_obj.name, 'test.txt')
        
        # Verify chunks were created
        chunks = Chunk.objects.filter(file=file_obj)
        self.assertGreater(chunks.count(), 0)
        
        # Verify S3 calls
        self.assertGreater(mock_s3.upload_fileobj.call_count, 0)
    
    @patch('storage.views.get_s3_client')
    def test_download_file(self, mock_get_s3_client):
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_get_s3_client.return_value = mock_s3
        
        # Set up download behavior
        def side_effect(bucket, key, fileobj):
            fileobj.write(b"Chunk content")
        mock_s3.download_fileobj.side_effect = side_effect
        
        # Create file and chunks
        file_obj = File.objects.create(
            name='test.txt',
            size=1000,
            owner=self.user
        )
        chunk = Chunk.objects.create(
            file=file_obj,
            chunk_number=0,
            checksum=hashlib.sha256(b"Chunk content").hexdigest(),
            size=len(b"Chunk content")
        )
        chunk.nodes.add(self.node1)
        
        # Download the file
        response = self.client.get(reverse('download', args=[file_obj.id]))
        
        # Check response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'], f'attachment; filename="{file_obj.name}"')
        
        # Verify S3 calls
        mock_s3.download_fileobj.assert_called()
