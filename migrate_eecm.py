"""
migrate_eecm.py — ترحيل بيانات EECM من الكود القديم إلى الـ schema الجديدة (v2)

التشغيل:
    export EECM_OWNER_EMAIL="بريدك"
    export EECM_OWNER_PASSWORD="باسورد قوي"
    python migrate_eecm.py
"""

import os
import json
from flask import Flask
from db_init import init_db
from models import (
    db, Tenant, User, Page, Product, Policy,
    BotConfig, Keyword, BotAppId, FollowupStage
)

app = Flask(__name__)
init_db(app)


EECM_PRODUCTS = [
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
         price_type="single", price_amount=450, shipping=0,
         price_note="450 ج شامل الشحن — قطعة واحدة",
         link="https://www.eecm.shop/products/removal-cream-new"),

    dict(key="wart_pen", name="قلم الزوائد الجلدية (يولا راي)",
         description="قلم لإزالة الزوائد الجلدية بدقة من الجسم",
         keywords="قلم زوائد,ثالول,لحمية",
         price_type="single", price_amount=450, shipping=0,
         price_note="450 ج شامل الشحن — قطعة واحدة",
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

# كلمات مفتاحية منقولة من app.py القديم
HUMAN_KEYWORDS = ["مدير", "موظف", "بشري", "إنسان حقيقي", "انسان حقيقي", "مسؤول", "human", "manager"]
COMPLAINT_KEYWORDS = [
    "غش", "غشاشين", "احتيال", "نصب", "نصاب", "نصابة", "نصابين", "بنصبوا", "تصابه",
    "كلب", "وسخ", "وسخه", "وسخة", "زبالة",
    "لا نافع", "مش نافع", "ما نفعش", "مش بيشتغل", "ما اشتغلش",
    "هعلن", "هبلغ", "هنشر", "على فيسيبوك", "على السوشيال",
    "قاضي", "القضاء", "محامي", "خسرت فلوس", "ضيعت فلوس",
    "مجاش", "مرجعش", "مش راضي خالص",
]


def run_migration():
    with app.app_context():
        existing = Tenant.query.filter_by(slug="eecm").first()
        if existing:
            print(f"⚠️  EECM tenant موجودة بالفعل (id={existing.id}) — لن يتم التكرار")
            return existing

        # ── Tenant ──
        tenant = Tenant(
            slug="eecm",
            business_name="EECM (Egyptian E-Commerce Medical)",
            business_description=(
                "شركة EECM متخصصة في بيع منتجات طبية طبيعية لحل مشاكل صحية شائعة "
                "(جلدية، عظام، أسنان، تنفسية) بدون الحاجة لزيارة طبيب في الحالات البسيطة. "
                "تستهدف العملاء في مصر عبر فيسبوك وانستجرام."
            ),
            industry="منتجات طبية طبيعية",
            plan="pro",
        )
        db.session.add(tenant)
        db.session.flush()
        print(f"✅ Tenant created: {tenant.business_name} (id={tenant.id})")

        # ── Owner account ──
        owner_email = os.environ.get("EECM_OWNER_EMAIL", "owner@eecm.shop")
        owner_password = os.environ.get("EECM_OWNER_PASSWORD", "ChangeMe123!")
        user = User(tenant_id=tenant.id, email=owner_email,
                    full_name="Eslam Elsayad", role="owner")
        user.set_password(owner_password)
        db.session.add(user)
        print(f"✅ Owner account: {owner_email} (غيّر الباسورد فوراً)")

        # ── Pages ──
        for platform, page_id, label in [
            ("page", "786079437911484", "YulaRay"),
            ("page", "767308839793152", "Junara"),
        ]:
            db.session.add(Page(
                tenant_id=tenant.id, platform=platform, page_id=page_id, label=label,
                access_token=os.environ.get(f"{label.upper()}_PAGE_ACCESS_TOKEN", ""),
            ))
        print("✅ 2 pages linked")

        # ── BotConfig (شخصية سارة) ──
        bot_config = BotConfig(
            tenant_id=tenant.id,
            bot_name="سارة", bot_age=28,
            bot_persona="موظفة مبيعات ودودة ومحترفة، شخصيتها دافئة وخفيفة الدم وعملية",
            dialect="مصري", tone="ودود وعملي",
            max_reply_lines=5, use_emojis=True,
            forbidden_words=json.dumps(["كيفك", "فيك", "شو", "هلق", "مشان"], ensure_ascii=False),
            forbidden_openers=json.dumps(
                ["بصراحة أنا موظفة مبيعات", "للأسف مش عندنا", "آسفة بس", "أنا مش متأكدة"],
                ensure_ascii=False),
            objection_expensive_response=(
                "قارني بالبديل الواقعي (دكتور/عيادة) واختمي بأن الدفع عند الاستلام = بدون مخاطرة"
            ),
            objection_unsure_response=(
                "اطمنيه بالضمان (استبدال/استرجاع) وبالمعاينة قبل الدفع"
            ),
            objection_later_response="أكدي محدودية الكمية وسهولة الطلب الآن بدون التزام مسبق",
            contact_number="01559516517",
            contact_channel="whatsapp",
            debounce_seconds=45,
            enable_vision=True,
        )
        db.session.add(bot_config)
        print("✅ BotConfig created (debounce=45s)")

        # ── Policy ──
        policy = Policy(
            tenant_id=tenant.id,
            payment_method="الدفع عند الاستلام (COD)",
            delivery_days="1 إلى 3 أيام عمل",
            return_policy="الاستبدال والاسترجاع متاح ومضمون",
            exchange_policy="استبدال خلال 14 يوم من الاستلام",
            inspection_policy=(
                "العميل يفتح الكرتونة ويعاين المنتج بصرياً قبل الدفع، "
                "المندوب بيدي وقت للمعاينة البصرية بس، مش بيستنى تجربة فعلية للمنتج"
            ),
            enable_sensitive_area_warning=True,
            enable_chronic_disease_warning=True,
            enable_followup=True,
            enable_installments=False,
        )
        db.session.add(policy)
        print("✅ Policy created")

        # ── Products ──
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

        # ── Keywords ──
        for kw in HUMAN_KEYWORDS:
            db.session.add(Keyword(tenant_id=tenant.id, category="human", value=kw))
        for kw in COMPLAINT_KEYWORDS:
            db.session.add(Keyword(tenant_id=tenant.id, category="complaint", value=kw))

        # كلمات الاعتراضات (بتغذي: رصد الاعتراض بالنوع + العروض الديناميكية + رؤى المنتجات)
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
        obj_count = 0
        for cat, kws in OBJECTION_KEYWORDS.items():
            for kw in kws:
                db.session.add(Keyword(tenant_id=tenant.id, category=cat, value=kw))
                obj_count += 1
        print(f"✅ {len(HUMAN_KEYWORDS)} human + {len(COMPLAINT_KEYWORDS)} complaint + {obj_count} objection keywords")

        # ── Bot App IDs (من اللوج اللي حللناه قبل كده) ──
        for app_id, label in [
            ("2579055582548571", "بوتنا الرئيسي — Claude/سارة"),
            ("1114622656624927", "Facebook Instant Reply"),
        ]:
            db.session.add(BotAppId(tenant_id=tenant.id, app_id=app_id, label=label))
        print("✅ 2 bot app IDs registered")

        # ── Followup Stages ──
        db.session.add(FollowupStage(
            tenant_id=tenant.id, stage_number=1, hours_after_last=24,
            message_text=(
                "أهلاً! 😊 سارة من EECM هنا.\n"
                "شايفة إنك اتكلمنا من شوية وأنا مش عايزاك تفوّت الفرصة دي.\n"
                "المنتج لسه متاح، التوصيل 1-3 أيام، والدفع عند الاستلام يعني مفيش أي مخاطرة.\n"
                "إيه اللي خلاك/ي تتردد/ي؟ أنا هنا أجاوب أي سؤال 💙"
            ),
            discount_percent=0,
        ))
        db.session.add(FollowupStage(
            tenant_id=tenant.id, stage_number=2, hours_after_last=12,   # +12h = 36h إجمالي
            message_text=(
                "أهلاً مجدداً! 🎁 سارة معاكي من EECM.\n"
                "عشان مهتم/ة بجد بمنتجاتنا، عندي مفاجأة ليك:\n"
                "لو طلبت دلوقتي هتاخد خصم 10% على طلبك ✅\n"
                "بس الخصم ده مش هيفضل طويل!\n"
                "الدفع عند الاستلام كالعادة. هتطلب دلوقتي؟ 😊"
            ),
            discount_percent=10,
        ))
        print("✅ 2 followup stages created (24h, +12h=36h total)")

        db.session.commit()
        print()
        print("🎉 الترحيل اكتمل بنجاح!")
        print(f"   Tenant ID: {tenant.id}")
        print(f"   Login: {owner_email}")
        return tenant


if __name__ == "__main__":
    run_migration()
