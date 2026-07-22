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

        print("\n=== 5. الكلمات المفتاحية الافتراضية (backfill) ===")
        # شكاوى (نصب/شتائم/تهديدات/فشل منتج) + طلب موظف + اعتراضات بالنوع.
        # بنضيف الناقص بس — اللي التاجر ضافه أو عدّله مابنلمسوش.
        try:
            from models import Tenant, Keyword
            import default_keywords
            added_total = 0
            for tenant in Tenant.query.all():
                added_total += default_keywords.seed_for_tenant(db, Keyword, tenant.id)
            if added_total:
                db.session.commit()
                print(f"  ✅ اتضاف {added_total} كلمة مفتاحية للحسابات الموجودة")
            else:
                print("  ⏭  كل الحسابات عندها الكلمات الافتراضية بالفعل")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ keywords backfill: {e}")
            ok_all = False

        print("\n=== 6. سلّم المتابعات الافتراضي (للحسابات الفاضية بس) ===")
        # بنزرع سلّم الـ 4 مراحل للحسابات اللي **مفيهاش أي مرحلة** بس
        # (حسابات جديدة اتعملت قبل ما create_tenant يبدأ يزرعها بنفسه).
        # اللي عنده مراحل — حتى لو أقل من 4 — مابنلمسوش: يمكن مسح عن قصد،
        # وزر "استعادة المراحل الافتراضية" في الداشبورد موجود للي مسح بالغلط.
        try:
            from models import Tenant, FollowupStage
            import default_keywords
            seeded_fu = 0
            for tenant in Tenant.query.all():
                if FollowupStage.query.filter_by(tenant_id=tenant.id).first():
                    continue   # عنده مراحل — مانلمسوش
                seeded_fu += default_keywords.seed_followup_stages_for_tenant(
                    db, FollowupStage, tenant.id)
            if seeded_fu:
                db.session.commit()
                print(f"  ✅ اتضاف {seeded_fu} مرحلة متابعة للحسابات الفاضية")
            else:
                print("  ⏭  مفيش حسابات فاضية من المراحل")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ followup stages seed: {e}")
            ok_all = False

        print("\n=== 7. الافتتاحيات الممنوعة الافتراضية ===")
        # جمل بتقتل البيعة من أول سطر (اعتذار/نفي/تشكيك/كشف إنها موظفة مبيعات).
        # بنضيفها للحسابات اللي حقلها فاضي بس — اللي عدّله التاجر مابنلمسوش.
        try:
            from models import BotConfig
            import json as _json
            DEFAULT_OPENERS = ["بصراحة أنا موظفة مبيعات", "للأسف مش عندنا",
                               "آسفة بس", "أنا مش متأكدة"]
            filled = 0
            for bc in BotConfig.query.all():
                current = (bc.forbidden_openers or "").strip()
                is_empty = current in ("", "[]", "null", "None")
                if is_empty:
                    bc.forbidden_openers = _json.dumps(DEFAULT_OPENERS, ensure_ascii=False)
                    filled += 1
            if filled:
                db.session.commit()
                print(f"  ✅ اتضافت الافتتاحيات الافتراضية لـ {filled} حساب")
            else:
                print("  ⏭  كل الحسابات عندها افتتاحيات بالفعل")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ forbidden openers backfill: {e}")
            ok_all = False

        print("\n=== 8. تصنيفات Meta: عمود الشرط المخصص + الافتراضيات ===")
        ok_all &= safe_alter(conn,
            "ALTER TABLE meta_labels ADD COLUMN custom_condition TEXT",
            "meta_labels.custom_condition")
        try:
            from models import Tenant, MetaLabel
            import default_keywords
            added_l = 0
            for tenant in Tenant.query.all():
                added_l += default_keywords.seed_labels_for_tenant(db, MetaLabel, tenant.id)
            if added_l:
                db.session.commit()
                print(f"  ✅ اتضاف {added_l} تصنيف افتراضي")
            else:
                print("  ⏭  كل الحسابات عندها التصنيفات الافتراضية")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ labels backfill: {e}")
            ok_all = False

        print("\n=== 9. النموذج الافتراضي → Haiku ===")
        # الحسابات القديمة اتزرعت بـ Sonnet كافتراضي. بنحوّلهم لـ Haiku
        # (أرخص ~3x وأداؤه قريب في مهام البيع). التاجر يقدر يرجّعه من الداشبورد.
        try:
            from models import BotConfig
            OLD_DEFAULT = "claude-sonnet-4-6"
            NEW_DEFAULT = "claude-haiku-4-5-20251001"
            switched = 0
            for bc in BotConfig.query.all():
                if (bc.model_name or "").strip() in ("", OLD_DEFAULT, "None"):
                    bc.model_name = NEW_DEFAULT
                    switched += 1
            if switched:
                db.session.commit()
                print(f"  ✅ {switched} حساب اتحوّل للنموذج الافتراضي Haiku")
            else:
                print("  ⏭  كل الحسابات على النموذج المطلوب")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ model default switch: {e}")
            ok_all = False

        print("\n=== 10. حملة يوم المرتبات — صف افتراضي لكل tenant ===")
        # الجدول نفسه بيتعمل من db.create_all() (جدول جديد مش عمود جديد).
        # هنا بنزرع بس صف افتراضي **معطّل** لكل tenant عشان صفحة الداشبورد
        # تلاقي إعدادات تعرضها — التاجر هو اللي بيفعّل الحملة بنفسه.
        try:
            from models import Tenant, SalaryCampaign
            seeded = 0
            for tenant in Tenant.query.all():
                if SalaryCampaign.query.filter_by(tenant_id=tenant.id).first():
                    continue
                db.session.add(SalaryCampaign(tenant_id=tenant.id))
                seeded += 1
            if seeded:
                db.session.commit()
                print(f"  ✅ اتضافت حملة مرتبات افتراضية (معطّلة) لـ {seeded} tenant")
            else:
                print("  ⏭  كل الـ tenants عندهم حملة مرتبات بالفعل")
        except Exception as e:
            db.session.rollback()
            print(f"  ⚠️ salary campaign seed: {e}")
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
