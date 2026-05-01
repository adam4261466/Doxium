#!/bin/sh
python delete_user_data.py
flask db upgrade
gunicorn "app:create_app()" --bind "0.0.0.0:${PORT:-8080}" --workers 2 --timeout 120
