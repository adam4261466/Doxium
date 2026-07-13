"""Entry point for the Celery worker process.

Called by: celery -A app.celery_worker.celery worker
Creates the Flask app in each forked worker via worker_process_init signal.
"""
from celery.signals import worker_process_init
from app.celery_app import celery, init_celery
import app.celery_app as celery_app_module


@worker_process_init.connect
def _setup_flask_app(**kwargs):
    from app import create_app
    celery_app_module.flask_app = create_app()
