"""
migrate_eecm.py — ترحيل بيانات EECM المكتوبة في الكود إلى الداتابيز كأول tenant

التشغيل (مرة واحدة فقط):
    python migrate_eecm.py

بعد التشغيل: EECM هتبقى موجودة كـ tenant كامل في الداتابيز،
وهتقدر تدير بياناتها من الداشبورد بدل تعديل الكود.
"""

import os
import sys
import json
from flask import Flask
from db_init import init_db
from models import db, Tenant, User, Page, Product, Policy

app = Flask(__name__)
init_db(app)


# =====================================================================
# البيانات المنقولة من app.py (PRODUCT_LINKS + PRODUCT_PRICES + النصوص)
# =====================================================================
EECM_PRODUCTS = [
    # key, name, description, keywords, price_type, price_amount, shipping, price_note, link
    dict(key="eczema", name="كريم الإكزيما",
         description="كريم طبيعي لعلاج الإكزيما والحكة والاحمرار في الجلد",
         keywords="اكزيما,حكة,التهاب جلدي,طفح جلدي",
         price_type="single", price_amount=399, shipping=50,
         price_note="449 ج (399 + 50 شحن) — قطعة واحدة",
         link="https://www.eecm.shop/products/eczema-treatment-cream"),

    dict(key="sleep", name="بخاخ النوم",
         description="بخاخ طبيعي يساعد على النوم العميق والاسترخاء",
         keywords="نوم,ارق,سهر,بخاخ نوم",
         price_type="single", price_amount=399, shipping=50,
         price_note="449 ج (399 + 50 شحن) — قطعة واحدة",
         link="https://www.eecm.shop/products/sleep-spray"),

    dict(key="dental", name="مجموعة خرز الأسنان",
         description="حل فوري لاستبدال الأسنان الناقصة أو المكسورة مؤقتاً بدون طبيب",
         keywords="اسنان,خرز,سن مكسور,سن ناقص,تركيب اسنان",
         price_type="single", price_amount=399, shipping=50,
         price_note="449 ج (399 + 50 شحن) — قطعة واحدة",
         link="https://www.eecm.shop/products/Dental-repair-beads-set"),

    dict(key="wart_cream", name="كريم الزوائد الجلدية (يولا راي)",
         description="كريم لإزالة الزوائد الجلدية من الجسم بأمان",
         keywords="زوائد جلدية,ثالول,لحمية,مزيل الثالول",
         price_type="single", price_amount=399, shipping=50,
         price_note="449 ج (399 + 50 شحن) — قطعة واحدة",
         link="https://www.eecm.shop/products/removal-cream-new"),

    dict(key="wart_pen", name="قلم الزوائد الجلدية (يولا راي)",
         description="قلم لإزالة الزوائد الجلدية بدقة من الجسم",
         keywords="قلم زوائد,ثالول,لحمية",
         price_type="single", price_amount=399, shipping=50,
         price_note="449 ج (399 + 50 شحن) — قطعة واحدة",
         link="https://www.eecm.shop/products/Pen-gel1"),

    dict(key="eye_wrinkle", name="كريم تجاعيد العين", sensitive_safe=True,
         sensitive_note="مصمم خصيصاً لمنطقة تحت العين — آمن",
         description="كريم لعلاج تجاعيد منطقة تحت العين",
         keywords="تجاعيد,تجاعيد العين,خطوط تحت العين",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/Eye-wrinkle-treatment-cream"),

    dict(key="acne", name="بخاخ حب الشباب",
         description="بخاخ طبيعي لعلاج حب الشباب",
         keywords="حب الشباب,بثور,حبوب الوجه",
         price_type="custom", price_amount=450, shipping=0,
         price_note="450 ج شامل الشحن — قطعة واحدة",
         link="https://www.eecm.shop/products/1Acne-treatment-spray1"),

    dict(key="paronychia", name="زيت علاج الداحس (غرس الأظافر)",
         description="زيت طبيعي لعلاج غرس الأظافر والداحس",
         keywords="داحس,غرس اظافر,التهاب الظفر",
         price_type="custom", price_amount=450, shipping=0,
         price_note="450 ج شامل الشحن — قطعة واحدة",
         link="https://www.eecm.shop/products/anti-paronychia"),

    dict(key="fungus", name="سيروم فطريات الأظافر",
         description="سيروم لعلاج فطريات الأظافر",
         keywords="فطريات اظافر,فطار الاظافر",
         price_type="custom", price_amount=374, shipping=50,
         price_note="424 ج (374 + 50 شحن) — قطعة واحدة",
         link="https://www.eecm.shop/products/Nail-fungus-treatment-serum1"),

    dict(key="hemorrhoid", name="بخاخ البواسير", sensitive_safe=True,
         sensitive_note="آمن للاستخدام على المنطقة الشرجية",
         description="بخاخ طبيعي لعلاج البواسير والألم والحرقة",
         keywords="بواسير,شرجية,الم شرجي",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/1hemorrhoid-treatment-spray"),

    dict(key="psoriasis", name="كريم الصدفية",
         description="كريم طبيعي لعلاج الصدفية",
         keywords="صدفية",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/psoriasis-treatment-cream1"),

    dict(key="tinnitus", name="بخاخ طنين الأذن",
         description="بخاخ طبيعي لعلاج طنين الأذن",
         keywords="طنين,طنين الاذن",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/tanen1"),

    dict(key="lung", name="بخاخ تنظيف الرئة",
         description="بخاخ طبيعي لتنظيف وتنقية الرئة",
         keywords="تنظيف رئة,رئة,تنفس",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/lung-cleansing-spray1"),

    dict(key="numbness", name="كريم تنميل الأصابع",
         description="كريم لعلاج تنميل أصابع اليد والقدم والأطراف عموماً",
         keywords="تنميل,تنميل اصابع,تنميل اطراف,تنميل قدم,تنميل رجل",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/Alopecia-b-cream1"),

    dict(key="stretch", name="كريم علامات التمدد",
         description="كريم طبيعي لعلاج علامات التمدد",
         keywords="علامات تمدد,شقوق,ستريتش مارك",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/stretch-mark-removal-cream1"),

    dict(key="sinusitis", name="كريم الجيوب الأنفية", sensitive_safe=True,
         sensitive_note="يُستخدم على منطقة الأنف — آمن",
         description="كريم طبيعي لعلاج التهاب الجيوب الأنفية",
         keywords="جيوب انفية,التهاب الجيوب",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/Alopecia-rekoval-cream1"),

    dict(key="mouth", name="بخاخ الفم المنعش",
         description="بخاخ طبيعي لتنعيش الفم وعلاج رائحة الفم",
         keywords="رائحة الفم,منعش الفم",
         price_type="bogo", price_amount=550, shipping=0,
         price_note="550 ج شامل الشحن — قطعتين (عرض خاص: قطعة + قطعة مجاناً)",
         link="https://www.eecm.shop/products/Fresh-mouth-spray1"),
]


def run_migration():
    with app.app_context():
        # ── تحقق إن EECM مش متهجرة قبل كده ──
        existing = Tenant.query.filter_by(slug="eecm").first()
        if existing:
            print(f"⚠️  EECM tenant موجودة بالفعل (id={existing.id}) — لن يتم التكرار")
            return existing

        # ── إنشاء الـ Tenant ──
        tenant = Tenant(
            slug="eecm",
            business_name="EECM (Egyptian E-Commerce Medical)",
            bot_name="سارة",
            bot_age=28,
            bot_persona="موظفة مبيعات ودودة ومحترفة، شخصيتها دافئة وخفيفة الدم وعملية",
            dialect="مصري",
            whatsapp_number="01559516517",
            plan="pro",
        )
        db.session.add(tenant)
        db.session.flush()   # عشان ناخد tenant.id قبل الـ commit
        print(f"✅ Tenant created: {tenant.business_name} (id={tenant.id})")

        # ── حساب تسجيل دخول للمالك ──
        owner_email = os.environ.get("EECM_OWNER_EMAIL", "owner@eecm.shop")
        owner_password = os.environ.get("EECM_OWNER_PASSWORD", "ChangeMe123!")
        user = User(
            tenant_id=tenant.id,
            email=owner_email,
            full_name="Eslam Elsayad",
            role="owner",
        )
        user.set_password(owner_password)
        db.session.add(user)
        print(f"✅ Owner account created: {owner_email}")
        print(f"   ⚠️  غيّر الباسورد فوراً بعد أول دخول — الافتراضي: {owner_password}")

        # ── الصفحات (لازم تحط التوكنات الحقيقية بعد كده من الداشبورد) ──
        pages_data = [
            ("page", "786079437911484", "YulaRay"),
            ("page", "767308839793152", "Junara"),
        ]
        for platform, page_id, label in pages_data:
            page = Page(tenant_id=tenant.id, platform=platform,
                        page_id=page_id, label=label,
                        access_token=os.environ.get(f"{label.upper()}_PAGE_ACCESS_TOKEN", ""))
            db.session.add(page)
        print(f"✅ {len(pages_data)} pages linked")

        # ── السياسات ──
        policy = Policy(
            tenant_id=tenant.id,
            payment_method="الدفع عند الاستلام (COD)",
            delivery_days="1 إلى 3 أيام عمل",
            return_policy="الاستبدال والاسترجاع متاح ومضمون",
            exchange_policy="استبدال خلال 14 يوم من الاستلام",
            inspection_policy="العميل يفتح الكرتونة ويعاين المنتج بصرياً قبل الدفع، "
                               "المندوب بيدي وقت للمعاينة البصرية بس، مش بيستنى تجربة فعلية للمنتج",
            enable_sensitive_area_warning=True,
            enable_chronic_disease_warning=True,
            enable_followup=True,
            followup_discount_percent=10,
        )
        db.session.add(policy)
        print("✅ Policy created")

        # ── المنتجات ──
        for p in EECM_PRODUCTS:
            product = Product(
                tenant_id=tenant.id,
                product_key=p["key"],
                name=p["name"],
                description=p["description"],
                keywords=p["keywords"],
                price_type=p["price_type"],
                price_amount=p["price_amount"],
                shipping_fee=p["shipping"],
                price_note=p["price_note"],
                product_link=p["link"],
                sensitive_area_safe=p.get("sensitive_safe", False),
                sensitive_area_note=p.get("sensitive_note"),
            )
            db.session.add(product)
        print(f"✅ {len(EECM_PRODUCTS)} products migrated")

        db.session.commit()
        print()
        print("🎉 الترحيل اكتمل بنجاح!")
        print(f"   Tenant ID: {tenant.id}")
        print(f"   Login: {owner_email}")
        return tenant


if __name__ == "__main__":
    run_migration()
