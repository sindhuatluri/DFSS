#!/usr/bin/env python3
"""
Cross-platform script to start the Distributed File Storage System locally.
This script replaces both start-local.sh (Unix) and start-local.ps1 (Windows).
"""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

# ANSI colors for Unix terminals
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def print_colored(message, color=Colors.BLUE, is_windows=False):
    """Print colored text (if on Unix) or plain text (if on Windows)"""
    if is_windows:
        print(message)
    else:
        print(f"{color}{message}{Colors.ENDC}")

def run_command(command, shell=True, check=True, capture_output=False):
    """Run a shell command and handle errors"""
    try:
        result = subprocess.run(
            command,
            shell=shell,
            check=check,
            capture_output=capture_output,
            text=True
        )
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {command}")
        print(f"Return code: {e.returncode}")
        if e.stdout:
            print(f"Standard output:\n{e.stdout}")
        if e.stderr:
            print(f"Standard error:\n{e.stderr}")
        sys.exit(1)

def activate_venv(is_windows):
    """Activate virtual environment if it exists"""
    venv_path = Path("venv")
    
    if venv_path.exists():
        print_colored("Activating virtual environment...", Colors.GREEN, is_windows)
        
        # We can't directly activate the venv in the current process from Python
        # Instead, we'll modify the PATH to include the venv's bin directory
        if is_windows:
            venv_bin = venv_path / "Scripts"
        else:
            venv_bin = venv_path / "bin"
            
        if venv_bin.exists():
            # Add the venv bin directory to the beginning of PATH
            os.environ["PATH"] = f"{venv_bin}{os.pathsep}{os.environ['PATH']}"
            
            # Also set VIRTUAL_ENV environment variable
            os.environ["VIRTUAL_ENV"] = str(venv_path.absolute())
            
            print_colored("Virtual environment activated", Colors.GREEN, is_windows)
        else:
            print_colored(f"Virtual environment bin directory not found at {venv_bin}", 
                         Colors.YELLOW, is_windows)
    else:
        print_colored("No virtual environment found. Using system Python.", 
                     Colors.YELLOW, is_windows)

def print_python_info():
    """Print Python version and environment information"""
    print_colored("\nPython version:", Colors.HEADER, is_windows)
    run_command("python --version")
    
    print_colored("Environment variables:", Colors.HEADER, is_windows)
    pythonpath = os.environ.get("PYTHONPATH", "")
    print(f"PYTHONPATH={pythonpath}")
    print()

def start_docker_compose():
    """Start Docker Compose services"""
    print_colored("Starting docker compose...", Colors.HEADER, is_windows)
    run_command("docker-compose up -d")

def check_required_packages(is_windows):
    """Check for required packages"""
    print_colored("\nChecking for required packages...", Colors.HEADER, is_windows)
    
    if is_windows:
        # PowerShell equivalent using Python
        result = run_command("pip list", capture_output=True)
        for package in ["boto3", "django", "django-storages"]:
            if package in result.stdout:
                print(f"Package found: {package}")
    else:
        # Unix grep approach
        run_command("pip list | grep -E 'boto3|django|django-storages'")

def run_migrations():
    """Run Django migrations"""
    print_colored("\nCreating migrations...", Colors.HEADER, is_windows)
    run_command("python manage.py makemigrations storage --verbosity 2")
    
    print_colored("\nApplying migrations...", Colors.HEADER, is_windows)
    run_command("python manage.py migrate --verbosity 2")

def seed_minio_nodes():
    """Seed MinIO nodes"""
    print_colored("\nSeeding MinIO nodes...", Colors.HEADER, is_windows)
    run_command("python manage.py seed_nodes --verbosity 2")

def check_create_superuser():
    """Create superuser if it doesn't exist"""
    print_colored("\nChecking for superuser...", Colors.HEADER, is_windows)
    
    # Python code to check and create superuser if needed
    superuser_check = """
from django.contrib.auth.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
    print('Superuser created successfully')
else:
    print('Superuser already exists')
"""
    
    run_command(f'python manage.py shell -c "{superuser_check}"')

def test_minio_connection():
    """Test connection to MinIO"""
    print_colored("\nTesting MinIO connection...", Colors.HEADER, is_windows)
    
    minio_test = """
import boto3
s3 = boto3.client('s3', endpoint_url='http://localhost:9000', 
                 aws_access_key_id='minioadmin', 
                 aws_secret_access_key='minioadmin',
                 verify=False)
response = s3.list_buckets()
print('MinIO buckets:', [bucket['Name'] for bucket in response['Buckets']])
"""
    
    run_command(f'python -c "{minio_test}"')

def start_celery(is_windows):
    """Start Celery worker and beat scheduler"""
    print_colored("\nStarting Celery worker in background...", Colors.HEADER, is_windows)
    
    if is_windows:
        # Windows needs to use Start-Process or similar
        run_command('start /B celery -A filestore worker -l info --logfile=celery_worker.log')
    else:
        # Unix can use --detach
        run_command('celery -A filestore worker -l info --logfile=celery_worker.log --detach')
    
    print_colored("\nStarting Celery beat scheduler in background...", Colors.HEADER, is_windows)
    
    if is_windows:
        run_command('start /B celery -A filestore beat -l info --logfile=celery_beat.log')
    else:
        run_command('celery -A filestore beat -l info --logfile=celery_beat.log --detach')
    
    print_colored("Celery processes started. Logs available in celery_worker.log and celery_beat.log", 
                 Colors.GREEN, is_windows)

def start_django_server():
    """Start the Django development server"""
    print_colored("\nStarting Django development server...", Colors.HEADER, is_windows)
    print_colored("Access the web UI at http://localhost:8000/", Colors.GREEN, is_windows)
    print_colored("Log in with username: admin, password: admin123", Colors.GREEN, is_windows)
    
    # This will block until the server is stopped
    run_command("python manage.py runserver 0.0.0.0:8000")

if __name__ == "__main__":
    # Detect operating system
    is_windows = platform.system() == "Windows"
    
    # Set exit on error behavior
    if not is_windows:
        # In Unix, we want to exit on error
        os.environ["PYTHONIOENCODING"] = "utf-8"
    
    try:
        # Run all the setup steps
        activate_venv(is_windows)
        print_python_info()
        start_docker_compose()
        check_required_packages(is_windows)
        run_migrations()
        seed_minio_nodes()
        check_create_superuser()
        test_minio_connection()
        start_celery(is_windows)
        start_django_server()
    except KeyboardInterrupt:
        print_colored("\nScript interrupted by user. Exiting...", Colors.YELLOW, is_windows)
        sys.exit(0)
    except Exception as e:
        print_colored(f"\nError: {str(e)}", Colors.RED, is_windows)
        sys.exit(1)
