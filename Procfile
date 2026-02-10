web: gunicorn run:app --bind 0.0.0.0:$PORT
worker: celery -A app.celery:celery worker --loglevel=info
