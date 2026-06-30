"""
models.py — قاعدة بيانات منصة سارة AI متعددة الشركات (Multi-tenant)

البنية:
  Tenant      — الشركة نفسها (اسم، شخصية البوت، لهجة، رقم واتساب)
  User        — حساب تسجيل دخول الداشبورد (مرتبط بـ tenant)
  Page        — صفحة فيسبوك/انستجرام مربوطة بـ tenant معين
  Product     — منتج تابع لـ tenant (سعر، وصف، رابط، تحذيرات)
  Policy      — سياسات الشركة (استرجاع، استبدال، توصيل، دفع)
  Order       — الطلبات المسجّلة (بديل Google Sheet لكل الشركات)

كل الجداول فيها tenant_id كـ foreign key — العزل الكامل بين الشركات.
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
    slug          = db.Column(db.String(80), unique=True, nullable=False)   # eecm, my-store..
    business_name = db.Column(db.String(200), nullable=False)

    # هوية البوت
    bot_name      = db.Column(db.String(80), default="سارة")
    bot_age       = db.Column(db.Integer, default=28)
    bot_persona   = db.Column(db.Text, default="موظفة مبيعات ودودة ومحترفة")
    dialect       = db.Column(db.String(40), default="مصري")   # مصري / خليجي / شامي ...

    # بيانات تواصل
    whatsapp_number = db.Column(db.String(30))

    # حالة الاشتراك
    is_active     = db.Column(db.Boolean, default=True)
    plan          = db.Column(db.String(40), default="trial")   # trial / starter / pro

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    users    = db.relationship("User",    backref="tenant", cascade="all, delete-orphan")
    pages    = db.relationship("Page",    backref="tenant", cascade="all, delete-orphan")
    products = db.relationship("Product", backref="tenant", cascade="all, delete-orphan")
    policy   = db.relationship("Policy",  backref="tenant", uselist=False, cascade="all, delete-orphan")
    orders   = db.relationship("Order",   backref="tenant", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "slug": self.slug, "business_name": self.business_name,
            "bot_name": self.bot_name, "bot_age": self.bot_age,
            "bot_persona": self.bot_persona, "dialect": self.dialect,
            "whatsapp_number": self.whatsapp_number,
            "is_active": self.is_active, "plan": self.plan,
        }


# =====================================================================
# USER — حساب دخول الداشبورد (مالك الشركة / موظف)
# =====================================================================
class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_user_email"),)

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    email         = db.Column(db.String(200), nullable=False, index=True)
    password_hash = db.Column(db.String(300), nullable=False)
    full_name     = db.Column(db.String(150))
    role          = db.Column(db.String(20), default="owner")   # owner / staff
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    # Flask-Login يحتاج الخصائص دي
    @property
    def is_authenticated(self): return True
    @property
    def is_active_user(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return self.id


# =====================================================================
# PAGE — صفحة فيسبوك/انستجرام مربوطة بشركة
# =====================================================================
class Page(db.Model):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("page_id", name="uq_page_id"),)

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)
    platform      = db.Column(db.String(20), nullable=False)   # page / instagram
    page_id       = db.Column(db.String(60), nullable=False, index=True)   # Meta page ID
    label         = db.Column(db.String(120))                 # اسم وصفي زي "YulaRay"
    access_token  = db.Column(db.Text)                        # FB/IG access token
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "platform": self.platform,
            "page_id": self.page_id, "label": self.label,
        }


# =====================================================================
# PRODUCT — منتج تابع لشركة
# =====================================================================
class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "product_key", name="uq_tenant_product_key"),)

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False)

    product_key   = db.Column(db.String(80), nullable=False)   # مفتاح فريد داخل الشركة: eczema
    name          = db.Column(db.String(200), nullable=False)  # كريم الإكزيما
    description   = db.Column(db.Text)                         # وصف عام يستخدمه البوت
    keywords      = db.Column(db.Text)                         # كلمات trigger مفصولة بفاصلة (RAG)

    # تسعير
    price_type    = db.Column(db.String(20), default="single")  # single / bogo / custom
    price_amount  = db.Column(db.Numeric(10, 2))                 # السعر الأساسي
    shipping_fee  = db.Column(db.Numeric(10, 2), default=50)
    price_note    = db.Column(db.String(300))                    # نص جاهز للحقن في الـ prompt

    # روابط وصور
    product_link  = db.Column(db.String(500))
    image_urls    = db.Column(db.Text)        # JSON list من روابط الصور
    review_image_urls = db.Column(db.Text)    # JSON list من صور الريفيوهات

    # تحذيرات وأمان
    sensitive_area_safe   = db.Column(db.Boolean, default=False)  # مسموح بمناطق حساسة محددة
    sensitive_area_note   = db.Column(db.String(300))             # "آمن تحت العين" مثلاً

    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "product_key": self.product_key, "name": self.name,
            "description": self.description, "keywords": self.keywords,
            "price_type": self.price_type,
            "price_amount": float(self.price_amount) if self.price_amount else None,
            "shipping_fee": float(self.shipping_fee) if self.shipping_fee else None,
            "price_note": self.price_note, "product_link": self.product_link,
            "sensitive_area_safe": self.sensitive_area_safe,
            "sensitive_area_note": self.sensitive_area_note,
            "is_active": self.is_active,
        }


# =====================================================================
# POLICY — سياسات الشركة (واحدة لكل tenant)
# =====================================================================
class Policy(db.Model):
    __tablename__ = "policies"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, unique=True)

    payment_method     = db.Column(db.String(120), default="الدفع عند الاستلام (COD)")
    delivery_days      = db.Column(db.String(60),  default="1 إلى 3 أيام عمل")
    return_policy       = db.Column(db.Text, default="الاستبدال والاسترجاع متاح ومضمون")
    exchange_policy      = db.Column(db.Text, default="استبدال خلال 14 يوم من الاستلام")
    inspection_policy    = db.Column(db.Text,
        default="العميل يفتح الكرتونة ويعاين المنتج بصرياً قبل الدفع، بدون تجربة فعلية للمنتج أمام المندوب")

    # تفعيل/تعطيل القواعد الذكية
    enable_sensitive_area_warning  = db.Column(db.Boolean, default=True)
    enable_chronic_disease_warning = db.Column(db.Boolean, default=True)
    enable_followup                = db.Column(db.Boolean, default=True)
    followup_discount_percent      = db.Column(db.Integer, default=10)

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
            "followup_discount_percent": self.followup_discount_percent,
        }


# =====================================================================
# ORDER — الطلبات (بديل Google Sheet، شامل لكل الشركات)
# =====================================================================
class Order(db.Model):
    __tablename__ = "orders"

    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    tenant_id     = db.Column(db.String(36), db.ForeignKey("tenants.id"), nullable=False, index=True)

    customer_name    = db.Column(db.String(200))
    customer_phone   = db.Column(db.String(40))
    customer_address = db.Column(db.Text)
    product_name     = db.Column(db.String(300))
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
