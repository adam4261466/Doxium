web: gunicorn "app:create_app()" --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120
worker: celery -A app.celery_app.celery worker --loglevel=info --concurrency=2 -P prefork
