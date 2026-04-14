from flask import Flask, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
import os

load_dotenv()

# ---------------------------------------
# Extensions
# ---------------------------------------
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
mail = Mail()

# Rate limiting per IP
# Rate limiting per IP with Redis storage
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60 per minute"],
    storage_uri=os.getenv("REDIS_URL", "redis://localhost:6379/1")
)


# Import after db initialization
from .models import User

# ---------------------------------------
# Flask-Login configuration
# ---------------------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(success=False, error="Unauthorized"), 401
    return redirect(url_for("main.login"))


# ---------------------------------------
# Application Factory
# ---------------------------------------
import logging

def get_database_uri():
    database_url = os.getenv("DATABASE_URL")
    public_url = os.getenv("DATABASE_PUBLIC_URL")

    chosen = public_url if database_url and "postgres.railway.internal" in database_url and public_url else database_url or public_url

    logging.warning("DATABASE_URL=%s PUBLIC_URL=%s chosen=%s", database_url, public_url, chosen)
    return chosen

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    database_uri = get_database_uri()
    if not database_uri:
        raise RuntimeError("DATABASE_URL or DATABASE_PUBLIC_URL is required")

    app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Uploads
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "data/uploads")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB

    # Cookies
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Mail Config
    app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
    app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
    app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "True") == "True"
    app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

    # Celery Defaults
    app.config.setdefault("CELERY_BROKER_URL", REDIS_URL)
    app.config.setdefault("CELERY_RESULT_BACKEND", REDIS_URL)

    # Init Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)

    # Session Injection for templates
    @app.context_processor
    def inject_session():
        from flask import session
        return dict(session=session)

    # Register Blueprints
    from .routes import main
    app.register_blueprint(main)

    return app
