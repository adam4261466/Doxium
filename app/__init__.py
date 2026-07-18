import os
import logging
from flask import Flask, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
from sqlalchemy import text # Needed for the column fix

load_dotenv()

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
mail = Mail()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Force in-memory rate limiter. Redis is unreliable on Railway
# (connections drop mid-request), which crashes the entire app.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60 per minute"],
    storage_uri="memory://"
)

from .models import User

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(success=False, error="Unauthorized"), 401
    return redirect(url_for("main.login"))

def get_database_uri():
    database_url = os.getenv("DATABASE_URL")
    public_url = os.getenv("DATABASE_PUBLIC_URL")
    # Railway internal URLs can sometimes be finicky; this prioritizes the working one
    chosen = public_url if database_url and "postgres.railway.internal" in database_url and public_url else database_url or public_url
    logging.warning("DATABASE_URL Connection Attempted")
    return chosen

def create_app():
    # Use absolute path for templates to avoid TemplateNotFound errors
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'templates'))
    app = Flask(__name__, template_folder=template_dir, static_folder="static")
    
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

    database_uri = get_database_uri()
    if not database_uri:
        raise RuntimeError("DATABASE_URL or DATABASE_PUBLIC_URL is required")

    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,}
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "data/uploads")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
    app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
    app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True") == "True"
    app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")
    app.config.setdefault("CELERY_BROKER_URL", REDIS_URL)
    app.config.setdefault("CELERY_RESULT_BACKEND", REDIS_URL)

    db.init_app(app)

    with app.app_context():
        db.create_all()

        migrations = [
            'ALTER TABLE "users" ADD COLUMN IF NOT EXISTS query_count INTEGER DEFAULT 0',
            'ALTER TABLE "users" ADD COLUMN IF NOT EXISTS query_reset_date TIMESTAMP',
            'ALTER TABLE files ADD COLUMN IF NOT EXISTS content BYTEA',
            'ALTER TABLE files ADD COLUMN IF NOT EXISTS folder_id INTEGER',
            '''CREATE TABLE IF NOT EXISTS faiss_index_store (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                index_data BYTEA,
                metadata_json JSONB,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''',
            '''CREATE TABLE IF NOT EXISTS upload_usage (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                month_key VARCHAR(7) NOT NULL,
                upload_count INTEGER DEFAULT 0,
                UNIQUE(user_id, month_key)
            )''',
            '''CREATE TABLE IF NOT EXISTS folders (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                parent_id INTEGER REFERENCES folders(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''',
            '''CREATE TABLE IF NOT EXISTS tags (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                color VARCHAR(7) DEFAULT '#6366f1',
                UNIQUE(name, user_id)
            )''',
            '''CREATE TABLE IF NOT EXISTS file_tags (
                file_id INTEGER NOT NULL REFERENCES files(id),
                tag_id INTEGER NOT NULL REFERENCES tags(id),
                PRIMARY KEY(file_id, tag_id)
            )''',
        ]

        for sql in migrations:
            try:
                db.session.execute(text(sql))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logging.info("Migration skipped: %s", e)

    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)

    from .celery_app import init_celery
    init_celery(app)

    @app.context_processor
    def inject_session():
        from flask import session
        return dict(session=session)

    from .routes import main
    app.register_blueprint(main)

    return app
