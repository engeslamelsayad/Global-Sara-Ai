"""
create_tenant.py — إنشاء شركة (tenant) جديدة من الصفر مع أول مستخدم

التشغيل من Railway Shell:
    python create_tenant.py

أو بدون أسئلة:
    SLUG=trendora NAME="Trendora Store" EMAIL=owner@trendora.com PASSWORD=Pass123! python create_tenant.py
"""

import os
import json
from flask import Flask
from db_init import init_db
from models import db, Tenant, User, BotConfig, Policy

app = Flask(__name__)
init_db(app)


def prompt(label, env_key=None, default=None):
    val = os.environ.get(env_key, "") if env_key else ""
    if val:
        return val
    if default:
        inp = input(f"{label} [{default}]: ").strip()
        return inp or default
    return input(f"{label}: ").strip()


def run():
    with app.app_context():
        print("\n=== إنشاء شركة جديدة ===\n")

        slug         = prompt("slug الشركة (حروف إنجليزي وشرطة فقط، مثال: trendora)", "SLUG")
        business_name = prompt("اسم الشركة", "NAME")
        email        = prompt("إيميل المالك", "EMAIL")
        password     = prompt("الباسورد", "PASSWORD")
        bot_name     = prompt("اسم البوت", "BOT_NAME", default="سارة")
        dialect      = prompt("اللهجة", "DIALECT", default="مصري")

        # ── تحقق إن الـ slug مش موجود ──
        existing = Tenant.query.filter_by(slug=slug).first()
        if existing:
            print(f"\n❌ الـ slug '{slug}' موجود بالفعل. اختر اسم مختلف.")
            return

        # ── إنشاء الـ Tenant ──
        tenant = Tenant(
            slug=slug,
            business_name=business_name,
            plan="trial",
        )
        db.session.add(tenant)
        db.session.flush()

        # ── إعدادات البوت الافتراضية ──
        bot = BotConfig(
            tenant_id=tenant.id,
            bot_name=bot_name,
            bot_age=28,
            bot_persona=f"موظفة مبيعات ودودة ومحترفة في {business_name}",
            dialect=dialect,
            tone="ودود وعملي",
            # افتتاحيات ضعيفة بتقتل البيعة من أول سطر
            forbidden_openers=json.dumps(
                ["بصراحة أنا موظفة مبيعات", "للأسف مش عندنا", "آسفة بس", "أنا مش متأكدة"],
                ensure_ascii=False),
            debounce_seconds=45,
            enable_vision=True,
        )
        db.session.add(bot)

        # ── سياسات افتراضية ──
        policy = Policy(
            tenant_id=tenant.id,
            payment_method="الدفع عند الاستلام (COD)",
            delivery_days="1 إلى 3 أيام عمل",
            return_policy="الاستبدال والاسترجاع متاح ومضمون",
            exchange_policy="استبدال خلال 14 يوم من الاستلام",
            inspection_policy="العميل يعاين المنتج بصرياً قبل الدفع",
            enable_sensitive_area_warning=True,
            enable_chronic_disease_warning=True,
            enable_followup=True,
        )
        db.session.add(policy)

        # ── الكلمات المفتاحية الافتراضية ──
        # شكاوى (نصب/شتائم/تهديدات/فشل منتج) + طلب موظف + اعتراضات بالنوع
        import default_keywords
        from models import Keyword
        n_kw = default_keywords.seed_for_tenant(db, Keyword, tenant.id)
        print(f"   ✅ {n_kw} كلمة مفتاحية افتراضية")

        # ── تصنيفات Meta الافتراضية ──
        from models import MetaLabel
        n_lbl = default_keywords.seed_labels_for_tenant(db, MetaLabel, tenant.id)
        print(f"   ✅ {n_lbl} تصنيف افتراضي")

        # ── المستخدم الأول ──
        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=business_name,
            role="owner",
        )
        user.set_password(password)
        db.session.add(user)

        db.session.commit()

        print(f"""
✅ تم إنشاء الشركة بنجاح!
   الاسم: {business_name}
   Slug:  {slug}
   Login: {email}

الخطوات التالية في الداشبورد:
  1. أضف منتجاتك من صفحة "المنتجات"
  2. اربط صفحة فيسبوك/انستجرام من "الصفحات المربوطة"
  3. اضبط شخصية البوت من "شخصية البوت"
""")


if __name__ == "__main__":
    run()
