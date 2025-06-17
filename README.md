# Distributed File Storage System

A Django-based application that enables users to upload files, which are then chunked and distributed across multiple MinIO storage nodes for redundancy and scalability.

## Implementation Overview

1. **File Chunking**: Files are split into 5MB chunks, making it easier to distribute large files across nodes.
2. **Distributed Storage**: Chunks are stored across multiple MinIO nodes for redundancy.
3. **Replication**: Each chunk is stored on at least two different nodes to ensure fault tolerance.
4. **Deduplication**: The system avoids storing identical chunks by comparing checksums.
5. **Error Checking**: SHA-256 checksums verify data integrity during upload and download.
6. **Retrieval Optimization**: Files are retrieved from the least loaded nodes.
7. **Web Interface**: User-friendly dashboard for monitoring and file management.
8. **API Endpoints**: RESTful APIs for programmatic access to the system.
9. **Containerized Deployment**: Docker Compose setup for easy deployment.

## System Architecture

- **Frontend**: Django-based web application with user and admin interfaces
- **Storage Backend**: Multiple MinIO instances running in Docker containers as distributed nodes
- **Database**: sqlite3 for metadata storage

## Features

- **File Chunking**: Splits large files into smaller parts (5MB chunks by default)
- **Distributed Storage**: Stores chunks across multiple MinIO nodes
- **Metadata Tracking**: Centralized database for tracking file and chunk details
- **RESTful APIs**: Endpoints for upload, retrieval, and management
- **Replication**: Ensures redundancy by storing chunk copies on multiple nodes
- **Deduplication**: Avoids storing identical chunks
- **Error-Checking**: Uses SHA-256 checksums for data integrity
- **Retrieval Optimization**: Fetches files from the nearest or least-loaded node
- **Admin Interface**: Monitor node statuses and file distribution

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python3

## Development

### Directory Structure
```
filestore/
├── filestore/          # Django project settings
├── storage/            # Main Django app
├── docker-compose.yml  # Container orchestration
├── Dockerfile          # Django application container
└── requirements.txt    # Python dependencies
```

### Docker Services
- `minio1`, `minio2`, `minio3`: MinIO storage nodes

## Monitoring and Administration

Access the Django admin interface to:
- Monitor node status
- View file metadata
- Manage users and permissions

Access MinIO console for each node:
- http://localhost:9001/ - Node 1
- http://localhost:9003/ - Node 2
- http://localhost:9005/ - Node 3


# Setting Up the Distributed Storage System

This document provides detailed setup instructions and usage examples for the distributed file storage system.

## Setup Instructions

### Automatic Setup

```bash
# Make the script executable (Unix systems only)
chmod +x start-local.py

# Run the script on any platform (Windows, macOS, Linux)
python start-local.py
```

The Python script is the recommended approach because:
- **Cross-Platform**: Works on Windows, macOS, and Linux with the same command
- **Improved Error Handling**: Provides detailed error messages and graceful failure
- **Colored Output**: Uses colored terminal output on Unix systems for better readability
- **Automatic OS Detection**: Adjusts commands based on the detected operating system
- **Consistent Behavior**: Ensures the same setup process across all platforms

This script will:
- Activate the virtual environment (if it exists)
- Start Docker Compose
- Check for required packages
- Run Django migrations
- Seed MinIO nodes
- Create a superuser (admin/admin123) if it doesn't exist
- Start Celery worker and beat scheduler
- Run the Django development server

### Manual Setup - (Optional)

1. `cd` into the project:
   ```bash
   cd filestore
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows, use: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Create a local database:
   ```bash
   python manage.py makemigrations storage
   python manage.py migrate
   ```

4. Seed the MinIO nodes:
   ```bash
   python manage.py seed_nodes
   ```

5. Create a superuser:
   ```bash
   python manage.py createsuperuser
   ```

6. Run the development server:
   ```bash
   python manage.py runserver
   ```


## Troubleshooting

### Node Status Issues

If a node appears offline or has connection issues:

1. Check MinIO node status:
   ```bash
   docker-compose ps minio1 minio2 minio3
   ```

2. View logs for a specific node:
   ```bash
   docker-compose logs minio1
   ```

3. Restart a node if needed:
   ```bash
   docker-compose restart minio1
   ```
