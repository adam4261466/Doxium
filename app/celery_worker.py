"""Entry point for the Celery worker process.

Called by: celery -A app.celery_worker.celery worker
Creates the Flask app and wires up the Celery context so tasks
run inside an application context (required for SQLAlchemy, etc.).
"""
from app import create_app
from app.celery_app import celery, init_celery

app = create_app()
init_celery(app)
