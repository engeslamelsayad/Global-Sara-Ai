"""
migrate_add_columns.py — يضيف الأعمدة الجديدة لقاعدة البيانات الموجودة

المشكلة: db.create_all() بيعمل جداول جديدة فقط — مش بيعدّل جداول موجودة.
الحل: نضيف الأعمدة الناقصة يدوياً بـ ALTER TABLE (آمن — بيتجاهل لو العمود موجود).

بيشتغل تلقائياً عند كل إقلاع (من app_main) — كل أمر idempotent فالتكرار آمن.
ولسه ممكن تشغيله يدوياً من Railway Shell:
    python migrate_add_columns.py
"""

import os
from models import db

def safe_alter(conn, sql, label=None):
    """
    ينفّذ SQL في transaction منفصل ويتجاهل خطأ لو العمود/الجدول موجود.
    مهم: كل أمر في transaction لوحده — عشان لو واحد فشل، مايكسرش الباقي.
    (في PostgreSQL، فشل أمر جوه transaction بيوقف كل الأوامر اللي بعده)
    """
    label = label or sql[:60]
    trans = conn.begin()
    try:
        conn.execute(db.text(sql))
        trans.commit()
        print(f"  ✅ {label}")
        return True
    except Exception as e:
        trans.rollback()   # ← ده اللي بيصلّح مشكلة "transaction is aborted"
        err = str(e).lower()
        # خطأ "already exists" = عادي — يعني العمود موجود من قبل
        if any(kw in err for kw in ["already exists", "duplicate column", "duplicate"]):
            print(f"  ⏭  {label} (موجود بالفعل)")
            return True
        print(f"  ❌ {label}\n     {str(e)[:120]}")
        return False

def run(flask_app=None):
    """
    يشغّل كل الـ migrations.
    flask_app: لو متمرر (من app_main عند الإقلاع) بيستخدمه مباشرة.
               لو مش متمرر (تشغيل يدوي standalone) بينشئ app خاص بيه.
    """
    if flask_app is None:
        from flask import Flask
        from db_init import init_db
        flask_app = Flask(__name__)
        init_db(flask_app)

    with flask_app.app_context():
        is_pg = "postgresql" in str(db.engine.url) or "postgres" in str(db.engine.url)
        print(f"Database: {'PostgreSQL ✅' if is_pg else 'SQLite (تطوير محلي)'}\n")

        conn = db.engine.connect()
        ok_all = True

        print("=== 1. جدول users ===")
        ok_all &= safe_alter(conn,
            "ALTER TABLE users ADD COLUMN analytics_key VARCHAR(100)",
            "users.analytics_key")

        print("\n=== 1ب. جدول tenants (تليجرام) ===")
        for col, typ in [
            ("telegram_chat_id",   "VARCHAR(60)"),
            ("telegram_link_code", "VARCHAR(20)"),
            ("telegram_enabled",   "BOOLEAN DEFAULT FALSE"),
        ]:
            ok_all &= safe_alter(conn,
                f"ALTER TABLE tenants ADD COLUMN {col} {typ}",
                f"tenants.{col}")

        print("\n=== 1ج. جدول bot_configs (العروض الديناميكية) ===")
        for col, typ in [
            ("offer_hesitation_enabled",   "BOOLEAN DEFAULT FALSE"),
            ("offer_hesitation_threshold", "INTEGER DEFAULT 2"),
            ("offer_hesitation_percent",   "INTEGER DEFAULT 10"),
            ("offer_bundle_enabled",       "BOOLEAN DEFAULT FALSE"),
            ("offer_bundle_text",          "TEXT"),
        ]:
            ok_all &= safe_alter(conn,
                f"ALTER TABLE bot_configs ADD COLUMN {col} {typ}",
                f"bot_configs.{col}")

        print("\n=== 2. جدول products (الحقول الجديدة) ===")
        for col, typ in [
            ("features",         "TEXT"),
            ("who_benefits",     "TEXT"),
            ("results_timeline", "TEXT"),
            ("faq",              "TEXT"),
            ("cross_selling",    "TEXT"),
            ("closing_pitch",    "TEXT"),
        ]:
            ok_all &= safe_alter(conn,
                f"ALTER TABLE products ADD COLUMN {col} {typ}",
                f"products.{col}")

        print("\n=== 3. جدول smart_rules (جديد) ===")
        if is_pg:
            ok_all &= safe_alter(conn, """
                CREATE TABLE IF NOT EXISTS smart_rules (
                    id         VARCHAR(36) PRIMARY KEY,
                    tenant_id  VARCHAR(36) NOT NULL REFERENCES tenants(id),
                    rule_text  TEXT NOT NULL,
                    category   VARCHAR(40) DEFAULT 'custom',
                    is_active  BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )""", "smart_rules table")
            ok_all &= safe_alter(conn,
                "CREATE INDEX IF NOT EXISTS ix_smart_rules_tenant ON smart_rules(tenant_id)",
                "smart_rules index")
        else:
            print("  ⏭  SQLite: smart_rules ستُنشأ بـ db.create_all() تلقائياً")

        print("\n=== 4. جدول meta_labels (جديد) ===")
        if is_pg:
            ok_all &= safe_alter(conn, """
                CREATE TABLE IF NOT EXISTS meta_labels (
                    id             VARCHAR(36) PRIMARY KEY,
                    tenant_id      VARCHAR(36) NOT NULL REFERENCES tenants(id),
                    name           VARCHAR(120) NOT NULL,
                    trigger_stage  VARCHAR(40) DEFAULT 'none',
                    is_active      BOOLEAN DEFAULT TRUE,
                    meta_label_ids TEXT DEFAULT '{}',
                    created_at     TIMESTAMP DEFAULT NOW()
                )""", "meta_labels table")
            ok_all &= safe_alter(conn,
                "CREATE INDEX IF NOT EXISTS ix_meta_labels_tenant ON meta_labels(tenant_id)",
                "meta_labels index")
        else:
            print("  ⏭  SQLite: meta_labels ستُنشأ بـ db.create_all() تلقائياً")

        # مفيش conn.commit() هنا — كل أمر بيعمل commit لوحده في safe_alter
        conn.close()

        print()
        if ok_all:
            print("🎉 كل الـ migrations اكتملت بنجاح!")
            print("   الخطوة التالية: Railway بيعمل restart تلقائي — جرّب تسجيل الدخول دلوقتي.")
        else:
            print("⚠️  في بعض الأخطاء — راجع الرسائل فوق")

if __name__ == "__main__":
    run()
