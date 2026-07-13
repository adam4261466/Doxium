"""Entry point for the Celery worker process.

Called by: celery -A app.celery_worker.celery worker
Creates the Flask app and stores it in celery_app.flask_app
so tasks can access it for app context.
"""
import app.celery_app as celery_app_module
from app import create_app

app = create_app()

# Store on the module so forked workers inherit it via copy-on-write
celery_app_module.flask_app = app
