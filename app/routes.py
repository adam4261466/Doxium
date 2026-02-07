import os
import uuid
import re
import requests
import hmac
import hashlib
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify, send_file
from werkzeug.utils import secure_filename, safe_join
from .models import User, File, Chunk
from . import db, limiter
from flask_login import login_user, login_required, logout_user, current_user
from flask_limiter.util import get_remote_address
from .document_processor import extract_text
from .faiss_index import FaissIndex
from .embeddings import EmbeddingGenerator


main = Blueprint("main", __name__)

# Lemon Squeezy setup
LS_API_BASE = "https://api.lemonsqueezy.com/v1"

# -----------------------
# Home Route
# -----------------------
@main.route("/")
def home():
    return render_template("index.html", user=current_user)


# -----------------------
# Helpers
# -----------------------
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".html", ".json"
}
#ALLOWED_MIME_TYPES = {"text/plain", "text/markdown", "application/pdf"}

def allowed_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def check_storage_space(user_id, required_space=0):
    """Check if user has enough storage space. Returns (has_space, available_space_mb, used_space_mb)"""
    try:
        user = User.query.get(user_id)
        limit_mb = 1024.0 if user and user.is_pilot else 0.0  # 1GB for pilot, 0MB otherwise (no free tier)

        user_folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(user_id))
        if not os.path.exists(user_folder):
            return True, limit_mb, 0.0  # Assume limit available for new users

        # Calculate used space
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(user_folder):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if os.path.exists(filepath):
                    total_size += os.path.getsize(filepath)

        used_mb = total_size / (1024 * 1024)
        available_mb = limit_mb - used_mb

        has_space = available_mb >= (required_space / (1024 * 1024))
        return has_space, available_mb, used_mb
    except Exception as e:
        # If we can't check space, assume it's available
        limit_mb = 1024.0 if User.query.get(user_id) and User.query.get(user_id).is_pilot else 0.0
        return True, limit_mb, 0.0

def check_system_load():
    """Check if system is overloaded. Returns (is_overloaded, load_percentage)"""
    try:
        import psutil
        # Check CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)
        # Check memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        # Consider system overloaded if CPU > 90% or memory > 90%
        is_overloaded = cpu_percent > 90 or memory_percent > 90
        load_percentage = max(cpu_percent, memory_percent)
        return is_overloaded, load_percentage
    except ImportError:
        # psutil not available, assume not overloaded
        return False, 0.0
    except Exception as e:
        # If we can't check load, assume not overloaded
        return False, 0.0


# -----------------------
# Auth Routes
# -----------------------
from flask_mail import Message
from app import mail, csrf

@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        
        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("main.register"))
        if User.query.filter_by(email=email).first():
            flash("Email already exists.", "danger")
            return redirect(url_for("main.register"))
        
        # ✅ Create new user
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # ✅ Send welcome email
        msg = Message(
            subject="Welcome to DocHub 🚀",
            recipients=[new_user.email]
        )
        msg.body = f"""
        Hi {new_user.username},

        Welcome to DocHub! 🎉  
        You’re all set to start your pilot.

        👉 Book your 30-min setup call here:  
        https://calendly.com/yourname/30min  

        Best,  
        The DocHub Team
        """

        try:
            mail.send(msg)
            flash("Registration successful! Please check your email for setup instructions.", "success")
        except Exception as e:
            flash(f"Registration successful, but email failed to send: {e}", "warning")

        return redirect(url_for("main.login"))

    return render_template("register.html")

# -----------------------
# Login Route with Brute-force protection
# -----------------------
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded

# Define limiter decorator
failed_login_limit = limiter.limit("5 per minute", key_func=get_remote_address)

@main.route("/login", methods=["GET", "POST"])
@failed_login_limit
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            if user.is_pilot:
                flash("Welcome back, Pilot user!", "success")
                return redirect(url_for("main.dashboard"))
            else:
                flash("Logged in successfully. Upgrade to Pilot for access to all features.", "info")
                return redirect(url_for("main.pricing"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")



@main.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("main.login"))


@main.route("/create-checkout-session", methods=["POST"])
@login_required
@csrf.exempt
def create_checkout_session():
    if current_user.is_pilot:
        flash("You're already a Pilot member.", "info")
        return redirect(url_for("main.dashboard"))

    try:
        checkout_url = _create_ls_checkout_url(
            user_id=str(current_user.id),
            redirect_url=url_for("main.payment_success", _external=True),
        )
        return redirect(checkout_url, code=303)
    except Exception as e:
        current_app.logger.exception("Failed to create Lemon Squeezy checkout")
        return jsonify(error=str(e)), 400


@main.route("/create-checkout", methods=["POST"])
@login_required
@csrf.exempt
def create_checkout():
    if current_user.is_pilot:
        return jsonify({"error": "already_pilot"}), 400

    data = request.get_json(silent=True) or {}
    redirect_url = data.get("redirect_url") or url_for("main.payment_success", _external=True)

    try:
        checkout_url = _create_ls_checkout_url(
            user_id=str(current_user.id),
            redirect_url=redirect_url,
        )
        return jsonify({"checkout_url": checkout_url})
    except Exception as e:
        current_app.logger.exception("Failed to create Lemon Squeezy checkout")
        return jsonify({"error": "failed_to_create_checkout"}), 400


@main.route("/payment-success")
@login_required
def payment_success():
    if current_user.is_pilot:
        flash("You're all set! Your Pilot access is active.", "success")
        return redirect(url_for("main.dashboard"))

    # Lemon Squeezy webhooks are the source of truth for activation.
    return render_template("success.html", user=current_user)


@main.route("/api/me-status")
@login_required
def me_status():
    return jsonify(
        {
            "is_pilot": bool(current_user.is_pilot),
            "subscription_status": current_user.subscription_status,
        }
    )


@main.route("/pricing")
@login_required
def pricing():
    return render_template("pricing.html", user=current_user)


@main.route("/webhook", methods=["POST"])
@main.route("/webhook/lemonsqueezy", methods=["POST"])
@csrf.exempt
def lemonsqueezy_webhook():
    webhook_secret = os.getenv("LEMON_SQUEEZY_WEBHOOK_SECRET")
    if not webhook_secret:
        return jsonify(error="Webhook secret not configured"), 500

    payload = request.get_data()
    sig_header = (
        request.headers.get("X-Signature")
        or request.headers.get("X-Signature-256")
        or request.headers.get("X-Lemon-Signature")
    )
    if not sig_header:
        current_app.logger.warning("Webhook missing signature header. Headers: %s", list(request.headers.keys()))
        return jsonify(error="Missing signature"), 400

    computed = hmac.new(webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    sig_value = sig_header.split("=", 1)[-1] if "=" in sig_header else sig_header
    if not hmac.compare_digest(computed, sig_value):
        return jsonify(error="Invalid signature"), 400

    event_name = request.headers.get("X-Event-Name", "")
    event = request.get_json(silent=True) or {}
    data = event.get("data") or {}
    attrs = data.get("attributes") or {}
    meta = event.get("meta") or {}
    if not event_name:
        event_name = meta.get("event_name", "")
    if not event_name:
        event_name = event.get("event") or event.get("type") or ""
    event_name = event_name.lower()
    custom = meta.get("custom_data") or attrs.get("custom_data") or {}
    user_id = custom.get("user_id") or attrs.get("user_id")

    user = None
    if user_id:
        user = User.query.get(int(user_id))
    email = attrs.get("user_email") or attrs.get("customer_email") or attrs.get("email")
    if not user and email:
        user = User.query.filter_by(email=email).first()

    if not user:
        current_app.logger.warning(
            "Webhook user not found for event %s (user_id=%s, email=%s)",
            event_name,
            user_id,
            email,
        )
        return jsonify(success=True)

    if event_name == "order_created":
        _set_user_pilot(
            user,
            is_pilot=True,
            status="active",
            purchased_at=datetime.utcnow(),
        )
    elif event_name == "order_refunded":
        _set_user_pilot(
            user,
            is_pilot=False,
            status="refunded",
        )
    elif event_name.startswith("subscription_"):
        sub_id = attrs.get("id") or data.get("id")
        status = attrs.get("status") or event_name
        ends_at = attrs.get("ends_at") or attrs.get("renews_at")

        if event_name in ("subscription_created", "subscription_updated", "subscription_resumed", "subscription_payment_success"):
            _set_user_pilot(
                user,
                is_pilot=True,
                status=status,
                subscription_id=sub_id,
                expires_at=_parse_ls_datetime(ends_at),
            )
        elif event_name in ("subscription_cancelled", "subscription_canceled"):
            _set_user_pilot(
                user,
                is_pilot=True,  # Keep access during grace period if applicable
                status="cancelled",
                subscription_id=sub_id,
                expires_at=_parse_ls_datetime(ends_at),
            )
        elif event_name == "subscription_expired":
            _set_user_pilot(
                user,
                is_pilot=False,
                status="expired",
                subscription_id=sub_id,
                expires_at=_parse_ls_datetime(ends_at),
            )

    return jsonify(success=True)


def _ls_headers():
    api_key = os.getenv("LEMON_SQUEEZY_API_KEY")
    if not api_key:
        raise ValueError("LEMON_SQUEEZY_API_KEY not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }


def _create_ls_checkout_url(user_id: str, redirect_url: str) -> str:
    store_id = os.getenv("LEMON_SQUEEZY_STORE_ID")
    variant_id = os.getenv("LEMON_SQUEEZY_VARIANT_ID")
    if not store_id or not variant_id:
        raise ValueError("LEMON_SQUEEZY_STORE_ID/LEMON_SQUEEZY_VARIANT_ID not configured")

    if "?" in redirect_url:
        redirect_url = f"{redirect_url}&order_id=[order_id]&email=[email]"
    else:
        redirect_url = f"{redirect_url}?order_id=[order_id]&email=[email]"

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "product_options": {
                    "redirect_url": redirect_url,
                    "enabled_variants": [int(variant_id)],
                },
                "checkout_data": {
                    "custom": {
                        "user_id": user_id,
                    }
                },
            },
            "relationships": {
                "store": {
                    "data": {
                        "type": "stores",
                        "id": str(store_id),
                    }
                },
                "variant": {
                    "data": {
                        "type": "variants",
                        "id": str(variant_id),
                    }
                },
            },
        }
    }

    resp = requests.post(
        f"{LS_API_BASE}/checkouts",
        headers=_ls_headers(),
        json=payload,
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Lemon Squeezy checkout failed: {resp.text}")

    resp_json = resp.json()
    attrs = (resp_json.get("data") or {}).get("attributes", {})
    checkout_url = attrs.get("url") or attrs.get("checkout_url")
    if not checkout_url:
        raise RuntimeError("No checkout URL returned from Lemon Squeezy")
    return checkout_url




def _parse_ls_datetime(value):
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _set_user_pilot(user, is_pilot, status=None, subscription_id=None, expires_at=None, purchased_at=None):
    user.is_pilot = bool(is_pilot)
    if status:
        user.subscription_status = status
    if subscription_id:
        user.lemonsqueezy_subscription_id = str(subscription_id)
    if expires_at:
        user.subscription_expires_at = expires_at
    if purchased_at:
        user.pilot_purchased_at = purchased_at
    db.session.commit()


# -----------------------
# Dashboard + File Management
# -----------------------
@main.route("/dashboard")
@login_required
def dashboard():
    if not current_user.is_pilot:
        return redirect(url_for("main.pricing"))
    files_with_content = []
    for file in current_user.files:
        content = extract_text(file.path) if os.path.exists(file.path) else "No content available"
        files_with_content.append({
            "filename": file.filename,
            "size": file.size,
            "content": content,
            "id": file.id,
            "processed": file.processed
        })
    return render_template("dashboard.html", user=current_user, files=files_with_content, is_pilot=current_user.is_pilot)


@main.route("/upload", methods=["POST"])
@login_required
def upload():
    if not current_user.is_pilot:
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        return jsonify(success=False, error="Upgrade to Pilot to upload files.") if is_ajax else (flash("Upgrade to Pilot to upload files.", "warning"), redirect(url_for("main.pricing")))[1]

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    try:
        # Check system load first
        is_overloaded, load_percentage = check_system_load()
        if is_overloaded:
            msg = f"System is currently overloaded ({load_percentage:.1f}% utilization). Please try again in a few minutes."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        if "file" not in request.files:
            msg = "No file part"
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        f = request.files["file"]
        if f.filename == "":
            msg = "No selected file"
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        if not allowed_file(f.filename):
            msg = "Invalid file type. Only .txt, .md, .pdf, .docx, .pptx, .xlsx, .csv, .html, .json allowed."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        """if not allowed_mime_type(f):
            msg = "File content does not match the allowed type."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]
"""
        # Check for special characters in filename
        if re.search(r'[^a-zA-Z0-9._\- ]', f.filename):
            flash("Filename contains special characters. It will be displayed as is, but ensure compatibility.", "warning")

        original_filename = f.filename  # Preserve Unicode and original filename
        ext = os.path.splitext(original_filename)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}{ext}"
        user_folder = os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_user.id))
        os.makedirs(user_folder, exist_ok=True)
        filepath = os.path.join(user_folder, unique_filename)

        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)
        if size == 0:
            msg = "File is empty. Please upload a file with content."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]
        if size > 1 * 1024 * 1024 * 1024:
            msg = "File too large. Max size is 1 GB."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        # Check file count limit (max 50 files per user, 200 for pilot)
        user_file_count = File.query.filter_by(user_id=current_user.id).count()
        max_files = 200 if current_user.is_pilot else 0
        if user_file_count >= max_files:
            msg = f"You have reached the maximum limit of {max_files} files. Please delete some files before uploading new ones."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        # Check storage space
        has_space, available_mb, used_mb = check_storage_space(current_user.id, size)
        limit_mb = 1024.0 if current_user.is_pilot else 0.0
        if not has_space:
            msg = f"Storage limit exceeded. You have used {used_mb:.1f}MB of {limit_mb:.0f}MB. Available: {available_mb:.1f}MB. Upgrade to Pilot for access."
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        # Warn if storage is getting low (less than 10MB available)
        if available_mb < 10:
            flash(f"Warning: You have only {available_mb:.1f}MB of storage remaining. Consider deleting old files.", "warning")

        # Check for very large documents and suggest splitting
        if size > 5 * 1024 * 1024:  # 5MB
            flash(f"Large file detected ({size / (1024*1024):.1f}MB). Consider splitting large documents into smaller sections for better processing.", "info")

        f.save(filepath)
        if not os.path.exists(filepath):
            raise Exception("Failed to save file to disk")

        try:
            new_file = File(filename=original_filename, path=filepath, size=size, user_id=current_user.id)
            db.session.add(new_file)
            db.session.commit()
        except Exception as db_error:
            if 'filepath' in locals() and os.path.exists(filepath):
                try: os.remove(filepath)
                except: pass
            # Check if it's a database connection error
            if "connection" in str(db_error).lower() or "database" in str(db_error).lower():
                msg = "Database connection error. Please try again in a few moments. If the problem persists, contact support."
            else:
                msg = f"Upload failed: {str(db_error)}"
            return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]

        return jsonify(success=True, message="File uploaded successfully.") if is_ajax else (flash("File uploaded successfully.", "success"), redirect(url_for("main.dashboard")))[1]

    except Exception as e:
        if 'filepath' in locals() and os.path.exists(filepath):
            try: os.remove(filepath)
            except: pass
        # Check for system-level errors
        if "disk" in str(e).lower() or "space" in str(e).lower():
            msg = "Storage space is full. Please delete some files or contact support for storage upgrade."
        elif "permission" in str(e).lower():
            msg = "Permission denied. Please contact support if this persists."
        else:
            msg = f"Upload failed: {str(e)}"
        return jsonify(success=False, error=msg) if is_ajax else (flash(msg, "danger"), redirect(url_for("main.dashboard")))[1]


# -----------------------
# Process / Delete / View Routes
# -----------------------
@main.route("/process/<int:file_id>", methods=["POST"])
@login_required
def process_file_route(file_id):
    from .tasks import process_file_task
    if not current_user.is_pilot:
        flash("Upgrade to Pilot to process files.", "warning")
        return redirect(url_for("main.pricing"))

    file = File.query.filter_by(id=file_id, user_id=current_user.id).first_or_404()

    try:
        # Send the task to Celery instead of calling the function directly
        task = process_file_task.delay(file.id, current_user.id, current_app.config["UPLOAD_FOLDER"])
        flash("File is being processed in the background. You will be notified when it's done.", "info")
    except Exception as e:
        flash(f"Error sending file to background processing: {str(e)}", "danger")

    return redirect(url_for("main.dashboard"))





@main.route("/delete/<int:file_id>", methods=["POST"])
@login_required
def delete_file(file_id):
    from .tasks import rebuild_index_task
    if not current_user.is_pilot:
        flash("Upgrade to Pilot to delete files.", "warning")
        return redirect(url_for("main.pricing"))
    file = File.query.filter_by(id=file_id, user_id=current_user.id).first_or_404()
    was_processed = file.processed
    for chunk in file.chunks:
        db.session.delete(chunk)
    if os.path.exists(file.path):
        os.remove(file.path)
    db.session.delete(file)
    db.session.commit()

    if was_processed:
        try:
            # Run the rebuild asynchronously in the background
            rebuild_index_task.delay(current_user.id)
            flash("File deleted. FAISS index is rebuilding in the background.", "info")
        except Exception as e:
            flash("File deleted but FAISS index rebuild failed to start. Some search results may be inaccurate.", "warning")
    else:
        flash("File deleted successfully.", "success")
    
    return redirect(url_for("main.dashboard"))


@main.route("/view-file/<path:filepath>")
@login_required
def view_file(filepath: str):
    user_folder = safe_join(current_app.config["UPLOAD_FOLDER"], str(current_user.id))
    full_path = safe_join(user_folder, filepath)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        if not any(full_path.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
            flash("File type not allowed.")
            return redirect(url_for("main.dashboard"))
        return send_file(full_path, as_attachment=True, download_name=os.path.basename(filepath))
    flash("File not found.")
    return redirect(url_for("main.dashboard"))


# -----------------------
# Query Route with fallback and retry
# -----------------------


@main.route("/query", methods=["POST"])
@limiter.limit("2 per minute")
@login_required
def query_documents():
    if not current_user.is_pilot:
        flash("Upgrade to Pilot to query documents.", "warning")
        return redirect(url_for("main.pricing"))

    query_text = request.form.get("query")
    if not query_text:
        flash("Please enter a query.", "danger")
        return redirect(url_for("main.dashboard"))

    max_query_length = 1000
    if len(query_text.strip()) > max_query_length:
        flash(f"Your query is too long ({len(query_text)} characters). Please shorten it to {max_query_length} characters or less.", "warning")
        return redirect(url_for("main.dashboard"))

    if len(query_text.strip()) > 500:
        flash("Your query is quite long. Consider breaking it into smaller questions.", "info")

    from .tasks import generate_query_answer  # Add this import at top
    # INSTANT RESPONSE - Fire Celery task
    task = generate_query_answer.delay(current_user.id, query_text)
    flash("Generating answer... This may take 10-30 seconds.", "info")
    
    return redirect(url_for('main.query_status', task_id=task.id))

@main.route("/query/status/<task_id>")
@login_required
def query_status(task_id):
    from celery.result import AsyncResult
    from app.celery_app import celery
    
    result = AsyncResult(task_id, app=celery)
    
    if result.ready():
        if result.successful():
            data = result.get()
            if 'error' in data:
                flash(data['error'], "danger")
                return redirect(url_for("main.dashboard"))
            
            # ✅ Pass serializable data directly
            return render_template(
                "query_results.html",
                user=current_user,
                query=data['query'],
                answer=data['answer'],
                supporting_chunks=data['chunks']  # Already serializable dicts
            )
        else:
            flash("Query generation failed after retries. Please try again.", "danger")
            return redirect(url_for("main.dashboard"))
    
    return render_template("query_processing.html", task_id=task_id)


# -----------------------
# Admin Routes
# -----------------------
@main.route("/admin")
@login_required
def admin():
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.dashboard"))
    users = User.query.filter_by(is_pilot=True).all()
    return render_template("admin_pilots.html", users=users)


@main.route("/reset_index/<int:user_id>", methods=["POST"])
@csrf.exempt
@login_required
def reset_index(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.admin"))
    try:
        embedder = EmbeddingGenerator()
        faiss_index = FaissIndex(dim=embedder.get_dimension(), user_id=user_id)
        faiss_index.rebuild_index_from_chunks()
        flash(f"Index reset for user {user_id}.", "success")
    except Exception as e:
        flash(f"Error resetting index: {str(e)}", "danger")
    return redirect(url_for("main.admin"))


@main.route("/toggle-pilot/<int:user_id>", methods=["POST"])
@csrf.exempt
@login_required
def toggle_pilot(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.admin"))
    user = User.query.get_or_404(user_id)
    user.is_pilot = not user.is_pilot
    db.session.commit()
    flash(f"Pilot status for {user.username} updated.", "success")
    return redirect(url_for("main.admin"))


@main.route("/user-stats/<int:user_id>")
@login_required
def user_stats(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.admin"))
    user = User.query.get_or_404(user_id)
    file_count = File.query.filter_by(user_id=user.id).count()
    has_space, available_mb, used_mb = check_storage_space(user.id)
    return render_template("admin_user_stats.html", user=user, file_count=file_count, used_mb=used_mb, available_mb=available_mb)


@main.route("/impersonate/<int:user_id>", methods=["POST"])
@csrf.exempt
@login_required
def impersonate(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.admin"))
    user = User.query.get_or_404(user_id)
    # Store admin id in session for return
    from flask import session
    session['admin_id'] = current_user.id
    login_user(user)
    flash(f"You are now impersonating {user.username}.", "info")
    return redirect(url_for("main.dashboard"))


@main.route("/return-to-admin")
@login_required
def return_to_admin():
    from flask import session
    admin_id = session.get('admin_id')
    if admin_id:
        admin_user = User.query.get(admin_id)
        if admin_user and admin_user.is_admin:
            login_user(admin_user)
            session.pop('admin_id', None)
            flash("Returned to admin account.", "info")
            return redirect(url_for("main.admin"))
    flash("Unable to return to admin.", "danger")
    return redirect(url_for("main.dashboard"))


@main.route("/delete-user/<int:user_id>", methods=["POST"])
@csrf.exempt
@login_required
def delete_user(user_id):
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.admin"))
    user = User.query.get_or_404(user_id)
    # Delete files and chunks
    for file in user.files:
        for chunk in file.chunks:
            db.session.delete(chunk)
        if os.path.exists(file.path):
            os.remove(file.path)
        db.session.delete(file)
    # Delete user's uploads folder
    import shutil
    user_folder = os.path.join("data", "uploads", str(user_id))
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
    # Delete FAISS index folder
    faiss_dir = os.path.join("data", "faiss", f"{user_id}.faiss")
    if os.path.exists(faiss_dir):
        shutil.rmtree(faiss_dir)
    db.session.delete(user)
    db.session.commit()
    flash("User and their data deleted.", "danger")
    return redirect(url_for("main.admin"))


@main.route("/system-stats")
@login_required
def system_stats():
    if not current_user.is_admin:
        flash("Access denied.", "danger")
        return redirect(url_for("main.admin"))
    user_count = User.query.count()
    pilot_count = User.query.filter_by(is_pilot=True).count()
    file_count = File.query.count()
    chunk_count = Chunk.query.count()
    # Calculate total storage used
    total_used = 0
    for file in File.query.all():
        total_used += file.size
    total_used_mb = total_used / (1024 * 1024)
    return render_template("admin_stats.html", user_count=user_count, pilot_count=pilot_count, file_count=file_count, chunk_count=chunk_count, total_used_mb=total_used_mb)


# -----------------------
# Error Handlers
# -----------------------
@main.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@main.errorhandler(500)
def internal_server_error(e):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(success=False, error="Internal server error"), 500
    return render_template("500.html"), 500
