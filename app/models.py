from . import db
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # OLD (keep for now)
    is_pilot = db.Column(db.Boolean, default=False, nullable=True)
    pilot_purchased_at = db.Column(db.DateTime, nullable=True)

    # Admin
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    # ✅ NEW — Lemon Squeezy subscription fields
    subscription_status = db.Column(db.String(20), default="inactive")
    subscription_expires_at = db.Column(db.DateTime, nullable=True)
    lemonsqueezy_subscription_id = db.Column(db.String(100), nullable=True)

    # Relationship to files
    files = db.relationship('File', backref='user', lazy=True)
    query_count = db.Column(db.Integer, default=0)
    query_count_reset_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_monthly_file_count(self):
        from datetime import datetime, timezone
        start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return File.query.filter(
            File.user_id == self.id,
            File.created_at >= start_of_month
        ).count()
        
class FaissIndexStore(db.Model):
    __tablename__ = 'faiss_index_store'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, unique=True)
    index_data = db.Column(db.LargeBinary, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(),
                           onupdate=db.func.current_timestamp())
    
class BillingEvent(db.Model):
    __tablename__ = 'billing_events'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    event_type = db.Column(db.String(100), nullable=False)  # e.g. order_created
    amount = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(10), nullable=True)
    ls_order_id = db.Column(db.String(100), nullable=True)
    ls_subscription_id = db.Column(db.String(100), nullable=True)
    raw_payload = db.Column(db.JSON, nullable=True)  # full webhook payload
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())


class File(db.Model):
    __tablename__ = 'files'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    processed = db.Column(db.Boolean, default=False)
    file_metadata = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    chunks = db.relationship('Chunk', backref='file', lazy=True)

class Chunk(db.Model):
    __tablename__ = 'chunks'
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey('files.id'), nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    start_char = db.Column(db.Integer, default=0)
    end_char = db.Column(db.Integer, default=0)
    chunk_metadata = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

class IndexMeta(db.Model):
    __tablename__ = 'index_meta'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    index_path = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
