from celery import Celery
from celery.schedules import crontab

celery = Celery("app", include=["app.tasks"])

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    task_time_limit=300,
    task_soft_time_limit=240,
)


def init_celery(app):
    """Configure celery with the Flask app's settings. Called inside create_app()."""
    celery.conf.update(
        broker_url=app.config.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        result_backend=app.config.get("CELERY_RESULT_BACKEND",
                                      app.config.get("CELERY_BROKER_URL",
                                                     "redis://localhost:6379/0")),
    )

    celery.conf.beat_schedule = {
        "cleanup-expired-files-daily": {
            "task": "tasks.cleanup_expired_files",
            "schedule": crontab(hour=3, minute=0),
        },
    }

    class ContextTask(celery.Task):
        abstract = True
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return super().__call__(*args, **kwargs)

        def __protected_call__(self, *args, **kwargs):
            with app.app_context():
                return super().__protected_call__(*args, **kwargs)

    celery.Task = ContextTask
