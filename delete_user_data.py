import os
import shutil
from sqlalchemy import text
from app import create_app, db
from app.models import User, File, Chunk, IndexMeta, BillingEvent

app = create_app()

with app.app_context():
    try:
        db.session.query(Chunk).delete()
        db.session.commit()
        print("✅ Deleted all chunks")

        db.session.query(File).delete()
        db.session.commit()
        print("✅ Deleted all files")

        db.session.query(IndexMeta).delete()
        db.session.commit()
        print("✅ Deleted all index metadata")

        db.session.query(BillingEvent).delete()
        db.session.commit()
        print("✅ Deleted all billing events")

        db.session.query(User).delete()
        db.session.commit()
        print("✅ Deleted all users")

        db.session.execute(text("ALTER SEQUENCE users_id_seq RESTART WITH 1"))
        db.session.execute(text("ALTER SEQUENCE files_id_seq RESTART WITH 1"))
        db.session.execute(text("ALTER SEQUENCE chunks_id_seq RESTART WITH 1"))
        db.session.execute(text("ALTER SEQUENCE index_meta_id_seq RESTART WITH 1"))
        db.session.commit()
        print("🎉 Done!")

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error: {e}")

    data_dir = 'data'
    if os.path.exists(data_dir):
        for item in os.listdir(data_dir):
            item_path = os.path.join(data_dir, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        print("✅ Data folder cleared.")
    else:
        print("Data folder does not exist.")
