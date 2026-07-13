"""Entry point for the Celery worker process.

Called by: celery -A app.celery_worker.celery worker
"""
from app.celery_app import celery
