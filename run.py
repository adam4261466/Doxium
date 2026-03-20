"""import os
import psutil
import torch
from dotenv import load_dotenv
from app import create_app, db
from app.models import User

# Load environment variables
load_dotenv()

# =============================================
# Hardware Monitoring Setup
# =============================================
def print_hardware_info():
    print("\n" + "="*60)
    print("HARDWARE MONITORING")
    print("="*60)
    
    # CPU Info
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count()
    print(f"CPU Usage: {cpu_percent}% ({cpu_count} cores)")
    
    # Memory Info
    memory = psutil.virtual_memory()
    print(f"RAM Usage: {memory.percent}% ({memory.used / (1024**3):.1f}GB / {memory.total / (1024**3):.1f}GB)")
    
    # GPU Info
    if torch.cuda.is_available():
        print(f"GPU Available: {torch.cuda.get_device_name(0)}")
        print(f"GPU Memory: {torch.cuda.memory_allocated() / (1024**3):.1f}GB / {torch.cuda.get_device_properties(0).total_memory / (1024**3):.1f}GB")
    else:
        print("GPU Available: No CUDA device detected (using CPU)")
    
    # Disk Info
    disk = psutil.disk_usage('/')
    print(f"Disk Usage: {disk.percent}% ({disk.used / (1024**3):.1f}GB / {disk.total / (1024**3):.1f}GB)")
    
    print("="*60 + "\n")

app = create_app()

if __name__ == "__main__":
    # Print initial hardware info
    print_hardware_info()
    
    with app.app_context():
        print(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print("Creating tables...")
        db.create_all()  # Ensure tables are created
        print("Tables created successfully!\n")
    
    print("🚀 Starting Doxium application...")
    ########################app.run(debug=True)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)"""





















"""
from app import create_app, db

# Create Flask app for Gunicorn
app = create_app()

# MVP-safe: create tables on boot (OK for now)
with app.app_context():
    db.create_all()

"""
# doc-hub/run.py
import os
from app import create_app, db

app = create_app()

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

