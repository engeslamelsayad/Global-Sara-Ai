"""
db_init.py — تهيئة الاتصال بقاعدة البيانات

يستخدم DATABASE_URL من environment variables (Railway بيضيفه تلقائياً
لما تضيف PostgreSQL service من الداشبورد بتاعه)
"""

import os
from models import db


def init_db(app):
    db_url = os.environ.get("DATABASE_URL", "")

    # Railway بيدّي رابط بصيغة postgres:// لكن SQLAlchemy الحديث محتاج postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    if not db_url:
        # fallback محلي للتجربة بدون Postgres (SQLite)
        db_url = "sqlite:///local_dev.db"
        print("⚠️  DATABASE_URL غير موجود — استخدام SQLite محلي للتجربة فقط")

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,   # يتجنب أخطاء الاتصال المنقطع بعد فترة خمول
        "pool_recycle": 280,
    }

    db.init_app(app)

    with app.app_context():
        db.create_all()
        print("✅ Database tables ready")

    return db
