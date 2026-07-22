"""
default_keywords.py — الكلمات المفتاحية الافتراضية لأي حساب جديد

مصدر واحد للحقيقة: بيستخدمه create_tenant.py (حسابات جديدة)،
migrate_eecm.py (حساب EECM)، و migrate_add_columns.py (backfill للموجودين).

التصنيفات:
  human      → العميل بيطلب موظف بشري
  complaint  → شكوى / غضب / تهديد (البوت بيتوقف والموديريتور بيتنبّه)
  objection_expensive / objection_unsure / objection_later → اعتراضات بالنوع
"""

HUMAN_KEYWORDS = [
    # ⚠️ ملاحظة مهمة: المطابقة **جزئية** (الكلمة بتتلاقى جوّه أي جملة).
    # عشان كده ممنوع نحط كلمات مفردة زي "موظف" أو "مدير" أو "بشري" لوحدها —
    # لأن "أنا موظف وعايز الكريم" أو "الجلد البشري" هتحوّل عميل جاهز يشتري
    # لموظف بالغلط، والبوت بيسكت تماماً وقتها = بيعة ضايعة.
    # الحل: جمل سياقية بتغطي نفس النية من غير false positives.

    # طلب التحدث مع شخص
    "عايز أتكلم مع حد", "عايز اتكلم مع حد", "عاوز اتكلم مع حد",
    "أتكلم مع موظف", "اتكلم مع موظف", "اتكلم مع حد", "أتكلم مع حد",
    "اكلم موظف", "أكلم موظف", "اكلم حد", "أكلم حد",
    "في حد هنا", "فيه حد هنا", "في حد يرد", "حد يرد عليا", "حد يرد عليه",
    "في حد يساعدني", "فيه حد يساعدني",

    # طلب موظف/مسؤول/مدير — بسياق واضح
    "عايز موظف", "عاوز موظف", "أبغى موظف", "ابغى موظف", "أبي موظف",
    "موظف بشري", "موظف حقيقي", "مع موظف", "لموظف",
    "عايز مسؤول", "عاوز مسؤول", "مع مسؤول", "أبغى مسؤول",
    "عايز مدير", "عاوز مدير", "مع مدير", "المدير", "أبغى مدير",
    "عايز حد مسؤول", "حد مسؤول",

    # التأكيد إنه عايز إنسان مش بوت
    "إنسان حقيقي", "انسان حقيقي", "شخص حقيقي", "مش بوت", "مش روبوت",
    "بشري مش بوت", "عايز إنسان", "عايز انسان",

    # خدمة العملاء / التواصل مع الشركة
    "عايز خدمة العملاء", "خدمة العملاء", "خدمه العملاء",
    "أتواصل مع الشركة", "اتواصل مع الشركة", "أتواصل مع فريق", "اتواصل مع فريق",
    "رقم الشركة", "رقم تليفون",

    # إنجليزي (آمن — نادر جداً في محادثة عربية إلا للطلب ده)
    "human", "manager", "customer service", "real person", "agent",
]

# شكاوى: اتهامات نصب + شتائم + تهديدات (قانونية/سوشيال) + فشل المنتج
COMPLAINT_KEYWORDS = [
    # اتهامات نصب واحتيال
    "غش", "غشاشين", "احتيال", "نصب", "نصاب", "نصابة", "نصابين", "بنصبوا", "تصابه",
    # شتائم
    "كلب", "وسخ", "وسخه", "وسخة", "زبالة",
    # المنتج ما نفعش
    "لا نافع", "مش نافع", "ما نفعش", "مش بيشتغل", "ما اشتغلش",
    # تهديدات — أخطر فئة، محتاجة تدخل فوري
    "هعلن", "هبلغ", "هنشر", "على فيسيبوك", "على السوشيال",
    "قاضي", "القضاء", "محامي",
    # خسارة فلوس / طلب ما وصلش
    "خسرت فلوس", "ضيعت فلوس", "مجاش", "مرجعش", "مش راضي خالص",
]

OBJECTION_KEYWORDS = {
    "objection_expensive": [
        "غالي", "غاليه", "غالى", "كتير عليا", "كتير أوي", "مبالغ",
        "سعره كبير", "مش قد كده", "في أرخص", "فيه أرخص", "ارخص", "تخفيض", "خصم",
    ],
    "objection_unsure": [
        "مش متأكد", "مش متاكد", "مش واثق", "خايف", "قلقان", "هل بجد",
        "بجد بيجيب نتيجة", "مضمون", "نصاب", "نصب", "مش مقتنع", "محتار", "محتارة",
    ],
    "objection_later": [
        "بعدين", "هفكر", "افكر", "أفكر", "لما أشوف", "مش دلوقتي", "لسه",
        "هرجع لك", "هرجعلك", "أستأذن", "هشوف وأرد", "مش وقته",
    ],
}


def all_default_keywords():
    """بيرجّع [(category, value), ...] لكل الكلمات الافتراضية"""
    out = [("human", k) for k in HUMAN_KEYWORDS]
    out += [("complaint", k) for k in COMPLAINT_KEYWORDS]
    for cat, kws in OBJECTION_KEYWORDS.items():
        out += [(cat, k) for k in kws]
    return out


def seed_for_tenant(db, Keyword, tenant_id, skip_existing=True):
    """
    يزرع الكلمات الافتراضية لحساب معيّن. بيرجّع عدد اللي اتضاف.
    skip_existing: بيتخطى الموجود بالفعل (آمن للتكرار).
    """
    existing = set()
    if skip_existing:
        existing = {(k.category, k.value) for k in
                    Keyword.query.filter_by(tenant_id=tenant_id).all()}
    added = 0
    for cat, val in all_default_keywords():
        if (cat, val) in existing:
            continue
        db.session.add(Keyword(tenant_id=tenant_id, category=cat, value=val))
        added += 1
    return added


# ═══════════════════════════════════════════════════════════════════
# تصنيفات Meta الافتراضية (Labels)
# كل تصنيف مربوط بحالة — البوت بيحطه تلقائياً على العميل في الإنبوكس
# ═══════════════════════════════════════════════════════════════════
DEFAULT_LABELS = [
    ("مهتم",       "interested"),     # 🟢 أبدى اهتمام بمنتج
    ("اعتراض",     "objection"),      # 🟡 اعترض (غالي/مش متأكد)
    ("طلب",        "ordered"),        # ✅ سجّل طلب
    ("شكوى",       "complaint"),      # 🚨 اشتكى
    ("طلب موظف",   "human_needed"),   # 🙋 طلب موظف بشري
]


def seed_labels_for_tenant(db, MetaLabel, tenant_id, skip_existing=True):
    """يزرع تصنيفات Meta الافتراضية. بيرجّع عدد اللي اتضاف."""
    existing = set()
    if skip_existing:
        existing = {l.trigger_stage for l in
                    MetaLabel.query.filter_by(tenant_id=tenant_id).all()}
    added = 0
    for name, stage in DEFAULT_LABELS:
        # بنتخطى لو فيه تصنيف مربوط بنفس الحالة بالفعل (حتى لو اسمه مختلف)
        if stage in existing:
            continue
        db.session.add(MetaLabel(tenant_id=tenant_id, name=name,
                                 trigger_stage=stage, is_active=True))
        added += 1
    return added


# ═══════════════════════════════════════════════════════════════════
# سلّم المتابعات الافتراضي (4 مراحل ديناميكية)
# النصوص فاضية عمداً — الـ Smart Recovery بيبني الرسالة المخصصة
# حسب سبب توقف كل عميل (شاف السعر وسكت / اعترض / كان مهتم).
# ═══════════════════════════════════════════════════════════════════
DEFAULT_FOLLOWUP_STAGES = [
    # (رقم المرحلة، ساعات بعد آخر تفاعل، نسبة الخصم)
    (1, 6,  0),    # نكزة ودّية بعد 6 ساعات
    (2, 24, 0),    # رسالة قيمة بعد 24 ساعة
    (3, 24, 10),   # خصم 10% بعد 24 ساعة إضافية
    (4, 48, 10),   # آخر فرصة بعد 48 ساعة
]


def seed_followup_stages_for_tenant(db, FollowupStage, tenant_id):
    """
    يزرع مراحل المتابعة الافتراضية الناقصة بس (بأرقامها).
    اللي التاجر عدّله أو ضافه بنفسه مابنلمسوش. بيرجّع عدد اللي اتضاف.
    آمن للتكرار (idempotent) — بيتستخدم في:
      create_tenant.py (حسابات جديدة) + migrate_add_columns.py (backfill)
      + زر "استعادة المراحل الافتراضية" في الداشبورد.
    """
    existing_nums = {s.stage_number for s in
                     FollowupStage.query.filter_by(tenant_id=tenant_id).all()}
    added = 0
    for num, hours, discount in DEFAULT_FOLLOWUP_STAGES:
        if num in existing_nums:
            continue
        db.session.add(FollowupStage(
            tenant_id=tenant_id, stage_number=num,
            hours_after_last=hours, message_text="",
            discount_percent=discount,
        ))
        added += 1
    return added
