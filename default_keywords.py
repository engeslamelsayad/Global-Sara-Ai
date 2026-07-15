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
    "عايز أتكلم مع حد", "عايز اتكلم مع حد", "أتكلم مع موظف", "اتكلم مع موظف",
    "في حد هنا", "فيه حد هنا", "عايز موظف", "حد يرد عليا", "أبغى موظف",
    "أتواصل مع الشركة", "في حد يساعدني", "عايز خدمة العملاء", "خدمة العملاء",
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
