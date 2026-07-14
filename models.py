"""
models.py — قاعدة بيانات منصة AI Sales Moderator متعددة الشركات (Multi-tenant)

تحديثات هذه النسخة (v2):
  - BotConfig: شخصية البوت + سلوكه (قابل للتعديل بالكامل من الداشبورد)
  - Keyword: كلمات مفتاحية (شكاوى / طلب موظف / اعتراضات) — جدول قابل للإضافة والحذف
  - BotAppId: قائمة الـ App IDs الخاصة بالبوت/الأتمتة — أي echo من غيرهم = موديريتور بشري
  - FollowupStage: مراحل المتابعة (عدد غير محدود، كل مرحلة لها توقيت ورسالة وخصم مستقلين)
  - Policy: سياسات البزنس (دفع / توصيل / استرجاع)
  - Product: المنتجات بكل تفاصيلها

كل جدول مرتبط بـ tenant_id — عزل كامل بين الشركات.
"""

import uuid
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def gen_uuid():
    return str(uuid.uuid4())


# =====================================================================
# TENANT — الشركة نفسها
# =====================================================================
class Tenant(db.Model):
    __tablename__ = "tenants"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    slug          = db.Column(db.String(80), unique=True, nullable=False)
    business_name = db.Column(db.String(200), nullable=False)

    # وصف حر للبزنس — المالك بيكتبه، وبيُستخدم كأساس لاقتراحات الـ AI
    business_description = db.Column(db.Text)
    industry              = db.Column(db.String(120))

    # تكامل Google Sheet — رابط Apps Script Web App لاستقبال الطلبات
    google_sheet_url      = db.Column(db.String(500))

    # تكامل Telegram — لإرسال التقرير الأسبوعي للتاجر
    telegram_chat_id      = db.Column(db.String(60))    # معرّف محادثة التاجر
    telegram_link_code    = db.Column(db.String(20))    # كود مؤقت لربط الحساب
    telegram_enabled      = db.Column(db.Boolean, default=False)

    is_active     = db.Column(db.Boolean, default=True)
    plan          = db.Column(db.String(40), default="trial")
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    users         = db.relationship("User",          backref="tenant", cascade="all, delete-orphan")
    pages         = db.relationship("Page",           backref="tenant", cascade="all, delete-orphan")
    products      = db.relationship("Product",        backref="tenant", cascade="all, delete-orphan")
    policy        = db.relationship("Policy",         backref="tenant", uselist=False, cascade="all, delete-orphan")
    bot_config    = db.relationship("BotConfig",      backref="tenant", uselist=False, cascade="all, delete-orphan")
    keywords      = db.relationship("Keyword",        backref="tenant", cascade="all, delete-orphan")
    bot_app_ids   = db.relationship("BotAppId",       backref="tenant", cascade="all, delete-orphan")
    followups     = db.relationship("FollowupStage",  backref="tenant", cascade="all, delete-orphan",
                                     order_by="FollowupStage.stage_number")
    orders        = db.relationship("Order",          backref="tenant", cascade="all, delete-orphan")
    smart_rules   = db.relationship("SmartRule",      backref="tenant", cascade="all, delete-orphan",
                                     order_by="SmartRule.created_at")
    meta_labels   = db.relationship("MetaLabel",      backref="tenant", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "slug": self.slug, "business_name": self.business_name,
            "business_description": self.business_description, "industry": self.industry,
            "google_sheet_url": self.google_sheet_url,
            "is_active": self.is_active, "plan": self.plan,
        }


# =====================================================================
# USER — حساب دخول الداشبورد
# =====================================================================
class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_user_email"),)

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    email         = db.Column(db.String(200), nullable=False, index=True)
    password_hash = db.Column(db.String(300), nullable=False)
    full_name     = db.Column(db.String(150))
    role          = db.Column(db.String(20), default="owner")
    analytics_key = db.Column(db.String(100))   # مفتاح التحليلات — بيتحفظ للمستخدم يدخله مرة واحدة
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_authenticated(self): return True
    @property
    def is_active(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return self.id


# =====================================================================
# PAGE
# =====================================================================
class Page(db.Model):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("page_id", name="uq_page_id"),)

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    platform      = db.Column(db.String(20), nullable=False)
    page_id       = db.Column(db.String(60), nullable=False, index=True)
    label         = db.Column(db.String(120))
    access_token  = db.Column(db.Text)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "platform": self.platform,
                "page_id": self.page_id, "label": self.label, "is_active": self.is_active}


# =====================================================================
# BOT_CONFIG — شخصية البوت وسلوكه (قابل للتعديل بالكامل)
# =====================================================================
class BotConfig(db.Model):
    """كل حاجة بتخص 'شخصية' البوت وسلوكه التقني — مش بيانات البزنس نفسها"""
    __tablename__ = "bot_configs"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, unique=True)

    bot_name      = db.Column(db.String(80), default="سارة")
    bot_age       = db.Column(db.Integer, default=28)
    bot_persona   = db.Column(db.Text, default="موظفة مبيعات ودودة ومحترفة")
    dialect       = db.Column(db.String(40), default="مصري")
    tone          = db.Column(db.String(40), default="ودود وعملي")

    max_reply_lines   = db.Column(db.Integer, default=5)
    use_emojis        = db.Column(db.Boolean, default=True)
    forbidden_words    = db.Column(db.Text)
    forbidden_openers  = db.Column(db.Text)
    closing_reactions  = db.Column(db.Text, default='["👍","✅","🙏","👌","🤝","💪"]')

    objection_expensive_response = db.Column(db.Text)
    objection_unsure_response    = db.Column(db.Text)
    objection_later_response     = db.Column(db.Text)

    contact_number     = db.Column(db.String(30))
    contact_channel    = db.Column(db.String(20), default="whatsapp")

    debounce_seconds   = db.Column(db.Integer, default=45)
    enable_vision       = db.Column(db.Boolean, default=True)
    max_tokens          = db.Column(db.Integer, default=600)
    model_name           = db.Column(db.String(60), default="claude-sonnet-4-6")

    # ── العروض الديناميكية ──
    offer_hesitation_enabled   = db.Column(db.Boolean, default=False)   # خصم عند التردد
    offer_hesitation_threshold = db.Column(db.Integer, default=2)       # عدد الاعتراضات
    offer_hesitation_percent   = db.Column(db.Integer, default=10)      # نسبة الخصم
    offer_bundle_enabled       = db.Column(db.Boolean, default=False)   # عرض bundle لمنتجين
    offer_bundle_text          = db.Column(db.Text)                     # نص عرض الـ bundle

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        import json
        return {
            "bot_name": self.bot_name, "bot_age": self.bot_age,
            "bot_persona": self.bot_persona, "dialect": self.dialect, "tone": self.tone,
            "max_reply_lines": self.max_reply_lines, "use_emojis": self.use_emojis,
            "forbidden_words": json.loads(self.forbidden_words or "[]"),
            "forbidden_openers": json.loads(self.forbidden_openers or "[]"),
            "closing_reactions": json.loads(self.closing_reactions or "[]"),
            "objection_expensive_response": self.objection_expensive_response,
            "objection_unsure_response": self.objection_unsure_response,
            "objection_later_response": self.objection_later_response,
            "contact_number": self.contact_number, "contact_channel": self.contact_channel,
            "debounce_seconds": self.debounce_seconds, "enable_vision": self.enable_vision,
            "max_tokens": self.max_tokens, "model_name": self.model_name,
        }


# =====================================================================
# KEYWORD — كلمات مفتاحية قابلة للإضافة/الحذف من الداشبورد
# =====================================================================
class Keyword(db.Model):
    """
    category القيم المتاحة:
      human, complaint, objection_expensive, objection_unsure, objection_later
    """
    __tablename__ = "keywords"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    category      = db.Column(db.String(40), nullable=False, index=True)
    value         = db.Column(db.String(120), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "category": self.category, "value": self.value}


# =====================================================================
# BOT_APP_ID — App IDs الخاصة بالبوت/الأتمتة (لكشف الموديريتور البشري)
# =====================================================================
class BotAppId(db.Model):
    """أي echo بـ app_id مش هنا = رد موديريتور بشري → البوت يوقف فوراً لهذا العميل"""
    __tablename__ = "bot_app_ids"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    app_id        = db.Column(db.String(60), nullable=False)
    label         = db.Column(db.String(120))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "app_id": self.app_id, "label": self.label}


# =====================================================================
# FOLLOWUP_STAGE — مراحل المتابعة (عدد غير محدود)
# =====================================================================
class FollowupStage(db.Model):
    __tablename__ = "followup_stages"
    __table_args__ = (UniqueConstraint("tenant_id", "stage_number", name="uq_tenant_stage"),)

    id              = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id       = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    stage_number    = db.Column(db.Integer, nullable=False)
    hours_after_last = db.Column(db.Integer, nullable=False)
    message_text    = db.Column(db.Text, nullable=False)
    discount_percent = db.Column(db.Integer, default=0)
    is_active       = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id, "stage_number": self.stage_number,
            "hours_after_last": self.hours_after_last,
            "message_text": self.message_text,
            "discount_percent": self.discount_percent,
            "is_active": self.is_active,
        }


# =====================================================================
# PRODUCT
# =====================================================================
class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "product_key", name="uq_tenant_product_key"),)

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)

    product_key   = db.Column(db.String(80), nullable=False)
    name          = db.Column(db.String(200), nullable=False)
    description   = db.Column(db.Text)
    keywords      = db.Column(db.Text)

    price_type    = db.Column(db.String(20), default="single")
    price_amount  = db.Column(db.Numeric(10, 2))
    shipping_fee  = db.Column(db.Numeric(10, 2), default=50)
    price_note    = db.Column(db.String(300))

    product_link  = db.Column(db.String(500))
    image_urls    = db.Column(db.Text)
    review_image_urls = db.Column(db.Text)

    sensitive_area_safe = db.Column(db.Boolean, default=False)
    sensitive_area_note = db.Column(db.String(300))

    # ── حقول المحتوى التسويقي الجديدة ──
    features          = db.Column(db.Text)   # JSON list: ["ميزة 1", "ميزة 2"]
    who_benefits      = db.Column(db.Text)   # من يستفيد
    results_timeline  = db.Column(db.Text)   # متى تظهر النتيجة
    faq               = db.Column(db.Text)   # JSON list: [{"q":"سؤال","a":"جواب"}]
    cross_selling     = db.Column(db.Text)   # منتجات مكملة (مفصولة بفاصلة: key1,key2)
    closing_pitch     = db.Column(db.Text)   # نص إغلاق البيع

    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        import json
        return {
            "id": self.id, "product_key": self.product_key, "name": self.name,
            "description": self.description, "keywords": self.keywords,
            "price_type": self.price_type,
            "price_amount": float(self.price_amount) if self.price_amount else None,
            "shipping_fee": float(self.shipping_fee) if self.shipping_fee else None,
            "price_note": self.price_note, "product_link": self.product_link,
            "image_urls": json.loads(self.image_urls or "[]"),
            "review_image_urls": json.loads(self.review_image_urls or "[]"),
            "sensitive_area_safe": self.sensitive_area_safe,
            "sensitive_area_note": self.sensitive_area_note,
            "is_active": self.is_active,
        }


# =====================================================================
# POLICY
# =====================================================================
class Policy(db.Model):
    __tablename__ = "policies"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, unique=True)

    payment_method     = db.Column(db.String(120), default="الدفع عند الاستلام (COD)")
    delivery_days      = db.Column(db.String(60),  default="1 إلى 3 أيام عمل")
    return_policy       = db.Column(db.Text, default="الاستبدال والاسترجاع متاح ومضمون")
    exchange_policy      = db.Column(db.Text, default="استبدال خلال 14 يوم من الاستلام")
    inspection_policy    = db.Column(db.Text, default="العميل يعاين المنتج بصرياً قبل الدفع")

    enable_sensitive_area_warning  = db.Column(db.Boolean, default=True)
    enable_chronic_disease_warning = db.Column(db.Boolean, default=True)
    enable_followup                 = db.Column(db.Boolean, default=True)
    enable_installments              = db.Column(db.Boolean, default=False)

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "payment_method": self.payment_method, "delivery_days": self.delivery_days,
            "return_policy": self.return_policy, "exchange_policy": self.exchange_policy,
            "inspection_policy": self.inspection_policy,
            "enable_sensitive_area_warning": self.enable_sensitive_area_warning,
            "enable_chronic_disease_warning": self.enable_chronic_disease_warning,
            "enable_followup": self.enable_followup,
            "enable_installments": self.enable_installments,
        }


# =====================================================================
# ORDER
# =====================================================================
class Order(db.Model):
    __tablename__ = "orders"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)

    customer_name    = db.Column(db.String(200))
    customer_phone   = db.Column(db.String(40))
    customer_address = db.Column(db.Text)
    product_name     = db.Column(db.String(300))
    order_price      = db.Column(db.String(120))   # السعر النهائي شامل الكمية والخصم
    discount_code    = db.Column(db.String(40))
    page_id          = db.Column(db.String(60))

    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id, "customer_name": self.customer_name,
            "customer_phone": self.customer_phone, "customer_address": self.customer_address,
            "product_name": self.product_name, "discount_code": self.discount_code,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M"),
        }


# =====================================================================
# SMART RULE — قواعد ذكية مخصصة يضيفها مالك البزنس بحرية
# =====================================================================
class SmartRule(db.Model):
    """
    قاعدة ذكية بلغة طبيعية يكتبها مالك البزنس — بتتحقن في الـ system prompt مباشرةً.
    
    أمثلة:
      - "لو العميل قال إنه في الرياض قوليله التوصيل 24 ساعة بدل 3 أيام"
      - "لو سأل عن الضمان قوليله الضمان سنة كاملة من تاريخ الشراء"
      - "لا تذكري أي منافس بالاسم — لو العميل ذكر منافس قارني بالفايدة مش بالسعر"
    
    category القيم: sales / safety / behavior / custom
    """
    __tablename__ = "smart_rules"

    id          = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id   = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    rule_text   = db.Column(db.Text, nullable=False)
    category    = db.Column(db.String(40), default="custom")
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "rule_text": self.rule_text,
            "category": self.category, "is_active": self.is_active,
        }


# =====================================================================
# META_LABEL — تسمية labels مخصصة وربطها بحالات المحادثة
# =====================================================================
class MetaLabel(db.Model):
    """
    label يعرّفها مالك البزنس ويربطها بحالة (trigger) معينة.
    لما المحادثة توصل للحالة دي، البوت بيطبّق الـ label على العميل في Meta.

    trigger_stage القيم الممكنة:
      interested    — العميل أبدى اهتمام بمنتج
      objection     — العميل اعترض (غالي/مش متأكد)
      ordered       — العميل سجّل طلب
      complaint     — شكوى
      human_needed  — طلب موظف بشري
      none          — يدوي فقط (البوت مش بيطبّقها تلقائياً)
    """
    __tablename__ = "meta_labels"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)
    name          = db.Column(db.String(120), nullable=False)   # الاسم المعروض في Meta
    trigger_stage = db.Column(db.String(40), default="none")    # الحالة اللي بتفعّلها
    is_active     = db.Column(db.Boolean, default=True)
    # cache لـ label IDs على مستوى كل صفحة: {page_id: meta_label_id}
    meta_label_ids = db.Column(db.Text, default="{}")           # JSON
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        import json as _j
        return {
            "id": self.id, "name": self.name,
            "trigger_stage": self.trigger_stage, "is_active": self.is_active,
            "meta_label_ids": _j.loads(self.meta_label_ids or "{}"),
        }
