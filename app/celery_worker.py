"""Entry point for the Celery worker process.

Called by: celery -A app.celery_worker.celery worker
Creates the Flask app and stores it on the celery instance
so tasks can access it for app context.
"""
from app import create_app
from app.celery_app import celery, init_celery

app = create_app()
init_celery(app)

# Store Flask app on celery instance so tasks can access it
celery.flask_app = app
