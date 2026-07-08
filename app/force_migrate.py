from app import create_app, db
from sqlalchemy import text

app = create_app()
with app.app_context():
    print("--- Starting Manual Database Sync ---")
    
    # ADD THIS LINE - It creates the table if it's missing!
    db.create_all() 
    
    try:
        # This forces the columns to exist in PostgreSQL
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS query_count INTEGER DEFAULT 0;'))
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS query_reset_date TIMESTAMP;'))
        db.session.commit()
        print("--- Database Columns Added Successfully! ---")
    except Exception as e:
        print(f"--- Sync Info: {e} ---")
