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

        print("\n=== 1د. توسيع عمود tone (اقتراحات الـ AI طويلة) ===")
        # سبب 500 عند حفظ شخصية البوت: الـ AI بيقترح نبرة أطول من VARCHAR(40)
        if is_pg:
            ok_all &= safe_alter(conn,
                "ALTER TABLE bot_configs ALTER COLUMN tone TYPE VARCHAR(200)",
                "bot_configs.tone → VARCHAR(200)")
        else:
            print("  ⏭  SQLite — مافيش حد على طول النص")

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

        print("\n=== 2ب. جدول orders (عمود السعر) ===")
        ok_all &= safe_alter(conn,
            "ALTER TABLE orders ADD COLUMN order_price VARCHAR(120)",
            "orders.order_price")

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

        print("\n=== 5. كلمات الاعتراضات (backfill للـ tenants الموجودين) ===")
        # الاعتراضات (غالي/مش متأكد/بعدين) بتغذي: رصد الاعتراض بالنوع
        # + العروض الديناميكية + رؤى المنتجات. بنضيفها لأي tenant ناقصها.
        try:
            from models import Tenant, Keyword
            OBJECTION_KEYWORDS = {
                "objection_expensive": ["غالي", "غاليه", "غالى", "كتير عليا", "كتير أوي",
                                        "مبالغ", "سعره كبير", "مش قد كده", "في أرخص",
                                        "فيه أرخص", "ارخص", "تخفيض", "خصم"],
                "objection_unsure":    ["مش متأكد", "مش متاكد", "مش واثق", "خايف",
                                        "قلقان", "هل بجد", "بجد بيجيب نتيجة", "مضمون",
                                        "نصاب", "نصب", "مش مقتنع", "محتار", "محتارة"],
                "objection_later":     ["بعدين", "هفكر", "افكر", "أفكر", "لما أشوف",
                                        "مش دلوقتي", "لسه", "هرجع لك", "هرجعلك",
                                        "أستأذن", "هشوف وأرد", "مش وقته"],
            }
            added_total = 0
            for tenant in Tenant.query.all():
                existing = {(k.category, k.value) for k in
                            Keyword.query.filter_by(tenant_id=tenant.id).all()}
                for cat, kws in OBJECTION_KEYWORDS.items():
                    for kw in kws:
                        if (cat, kw) not in existing:
                            db.session.add(Keyword(tenant_id=tenant.id,
                                                   category=cat, value=kw))
                            added_total += 1
            if added_total:
                db.session.commit()
                print(f"  ✅ اتضاف {added_total} كلمة اعتراض للـ tenants الموجودين")
            else:
                print("  ⏭  كلمات الاعتراضات موجودة بالفعل")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ objection keywords backfill: {e}")
            ok_all = False

        print("\n=== 6. ترقية سلّم المتابعات لـ 4 مراحل ===")
        # الـ tenants القدام عندهم مرحلتين (24h + 12h خصم). نرقّيهم لسلّم الـ 4
        # مراحل الديناميكي (6/24/24+خصم/48+خصم) — بس لو لسه ماترقّوش.
        try:
            from models import Tenant, FollowupStage
            _target = [(1, 6, 0), (2, 24, 0), (3, 24, 10), (4, 48, 10)]
            upgraded = 0
            for tenant in Tenant.query.all():
                existing = FollowupStage.query.filter_by(tenant_id=tenant.id).all()
                # نرقّي بس لو عنده أقل من 4 مراحل (السلّم القديم أو ناقص)
                if len(existing) >= 4:
                    continue
                have_nums = {s.stage_number for s in existing}
                for num, hrs, disc in _target:
                    if num in have_nums:
                        continue
                    db.session.add(FollowupStage(
                        tenant_id=tenant.id, stage_number=num,
                        hours_after_last=hrs, message_text="", discount_percent=disc,
                    ))
                    upgraded += 1
                # نصلّح توقيت المرحلتين القديمتين لو مختلف (24→6 للأولى)
                for s in existing:
                    for num, hrs, disc in _target:
                        if s.stage_number == num and s.hours_after_last != hrs:
                            s.hours_after_last = hrs
                            s.discount_percent = disc
            if upgraded:
                db.session.commit()
                print(f"  ✅ اتضاف {upgraded} مرحلة متابعة (ترقية للسلّم الديناميكي)")
            else:
                print("  ⏭  كل الـ tenants عندهم السلّم الكامل بالفعل")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ followup stages upgrade: {e}")
            ok_all = False

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
