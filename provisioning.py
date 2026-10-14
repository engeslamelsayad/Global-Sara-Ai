"""
provisioning.py — إنشاء الحسابات (tenant + مستخدمين) من مكان واحد

بيستخدمه:
  - admin.py         (صفحة إدارة المستخدمين في الداشبورد)
  - create_tenant.py (السكربت القديم من Railway Shell — لسه شغال)

عشان الحساب الجديد ياخد نفس الافتراضيات بالظبط في الحالتين
(البوت، السياسات، الكلمات المفتاحية، التصنيفات، سلّم المتابعة).
"""

import json
import re
import secrets

from models import db, Tenant, User, BotConfig, Policy

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


class ProvisionError(ValueError):
    """خطأ في البيانات — رسالته بتتعرض للمستخدم زي ما هي"""


def normalize_email(email):
    # تسجيل الدخول بيعمل lower() — فلازم التخزين يبقى lower برضه
    return (email or "").strip().lower()


def generate_password():
    return secrets.token_urlsafe(9)   # ~12 حرف


def validate_email(email, exclude_user_id=None):
    if not EMAIL_RE.match(email):
        raise ProvisionError("الإيميل مش صحيح")
    q = User.query.filter(db.func.lower(User.email) == email)
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    if q.first():
        raise ProvisionError("الإيميل ده مستخدم بالفعل")


def validate_password(password):
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ProvisionError(f"الباسورد لازم يكون {MIN_PASSWORD_LEN} حروف على الأقل")


def create_user(tenant_id, email, password=None, full_name="", role="owner"):
    """
    يضيف مستخدم لشركة موجودة. لو مفيش باسورد بيتولّد واحد.
    بيرجّع (user, password) — الباسورد عشان يتعرض مرة واحدة للأدمن.
    مابيعملش commit.
    """
    email = normalize_email(email)
    validate_email(email)
    if not db.session.get(Tenant, tenant_id):
        raise ProvisionError("الشركة مش موجودة")
    password = password or generate_password()
    validate_password(password)

    user = User(tenant_id=tenant_id, email=email,
                full_name=(full_name or "").strip(), role=role or "owner")
    user.set_password(password)
    db.session.add(user)
    return user, password


def create_tenant(slug, business_name, owner_email, owner_password=None,
                  bot_name="سارة", dialect="مصري"):
    """
    شركة جديدة كاملة بكل الافتراضيات + أول مستخدم (owner).
    بيرجّع (tenant, user, password). مابيعملش commit.
    """
    slug = (slug or "").strip().lower()
    business_name = (business_name or "").strip()
    if not SLUG_RE.match(slug):
        raise ProvisionError("الـ slug لازم يكون حروف إنجليزي صغيرة وأرقام وشرطة بس (مثال: trendora)")
    if not business_name:
        raise ProvisionError("اسم الشركة مطلوب")
    if Tenant.query.filter_by(slug=slug).first():
        raise ProvisionError(f"الـ slug «{slug}» موجود بالفعل")
    # نتحقق من المستخدم قبل ما نعمل أي حاجة
    email = normalize_email(owner_email)
    validate_email(email)
    owner_password = owner_password or generate_password()
    validate_password(owner_password)

    tenant = Tenant(slug=slug, business_name=business_name, plan="trial")
    db.session.add(tenant)
    db.session.flush()

    db.session.add(BotConfig(
        tenant_id=tenant.id,
        bot_name=bot_name or "سارة",
        bot_age=28,
        bot_persona=f"موظفة مبيعات ودودة ومحترفة في {business_name}",
        dialect=dialect or "مصري",
        tone="ودود وعملي",
        # افتتاحيات ضعيفة بتقتل البيعة من أول سطر
        forbidden_openers=json.dumps(
            ["بصراحة أنا موظفة مبيعات", "للأسف مش عندنا", "آسفة بس", "أنا مش متأكدة"],
            ensure_ascii=False),
        debounce_seconds=45,
        enable_vision=True,
    ))

    db.session.add(Policy(
        tenant_id=tenant.id,
        payment_method="الدفع عند الاستلام (COD)",
        delivery_days="1 إلى 3 أيام عمل",
        return_policy="الاستبدال والاسترجاع متاح ومضمون",
        exchange_policy="استبدال خلال 14 يوم من الاستلام",
        inspection_policy="العميل يعاين المنتج بصرياً قبل الدفع",
        # التحذيرات الطبية معطّلة افتراضياً — خاصة بمنتجات العناية والعلاج.
        enable_sensitive_area_warning=False,
        enable_chronic_disease_warning=False,
        enable_followup=True,
    ))

    # الافتراضيات من المصدر الموحّد (القاعدة #2 في CLAUDE.md)
    import default_keywords
    from models import Keyword, MetaLabel, FollowupStage
    default_keywords.seed_for_tenant(db, Keyword, tenant.id)
    default_keywords.seed_labels_for_tenant(db, MetaLabel, tenant.id)
    default_keywords.seed_followup_stages_for_tenant(db, FollowupStage, tenant.id)

    user, password = create_user(tenant.id, email, owner_password,
                                 full_name=business_name, role="owner")
    return tenant, user, password
