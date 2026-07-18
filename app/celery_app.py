import logging
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
    broker = app.config.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    backend = app.config.get("CELERY_RESULT_BACKEND", broker)

    try:
        import redis as _redis
        r = _redis.from_url(broker, socket_connect_timeout=3, socket_timeout=3)
        r.ping()
        r.close()
        logging.info("Redis connected, enabling Celery with Redis broker")
        celery.conf.update(broker_url=broker, result_backend=backend)
    except Exception:
        logging.warning("Redis unavailable, Celery tasks will be disabled")
        celery.conf.update(broker_url="memory://", result_backend="cache+memory://")

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
