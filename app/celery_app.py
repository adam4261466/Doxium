from celery import Celery
from . import create_app


def make_celery(app):
    """Create and configure a Celery instance bound to the Flask app."""
    
    broker_url = app.config.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    result_backend = app.config.get("CELERY_RESULT_BACKEND", broker_url)

    celery = Celery(
        app.import_name,
        broker=broker_url,
        backend=result_backend,
        include=["app.tasks"],  # Import your task module
    )

    # Recommended stable config
    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        worker_concurrency=1,
        worker_prefetch_multiplier=1,
        worker_max_tasks_per_child=100,
        task_time_limit=300,          # Protection for heavy tasks
        task_soft_time_limit=240,
    )

    # Flask context inside Celery tasks
    class ContextTask(celery.Task):
        abstract = True
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return super().__call__(*args, **kwargs)

    celery.Task = ContextTask
    return celery


# Creates Flask and Celery instances for worker processes
flask_app = create_app()
celery = make_celery(flask_app)