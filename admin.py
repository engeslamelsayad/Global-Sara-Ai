"""
admin.py — إدارة المستخدمين والشركات (للأدمن الرئيسي بس) 🛠

بديل السكربتات اليدوية (create_user.py / create_tenant.py من Railway Shell):
صفحة /admin/users فيها:
  - كل المستخدمين الحاليين + تعديلهم (إيميل، اسم، شركة، دور، باسورد) + حذف
  - إضافة مستخدم لشركة موجودة
  - إنشاء شركة جديدة بكل الافتراضيات + أول مستخدم

الصلاحية: المستخدم اللي إيميله في SUPER_ADMIN_EMAILS (env، مفصولة بفاصلة).
الافتراضي: engeslamelsayad@gmail.com. أي حد تاني بياخد 404 (الصفحة كأنها مش موجودة).

⚠️ دي الصفحة الوحيدة اللي بتشوف كل الـ tenants — استثناء مقصود من قاعدة العزل (#10)،
ومحمية بالـ decorator تحت. ماتستخدمش الـ helpers دي في أي route تاني.

الـ API بيقبل JSON بس (request.is_json) — حماية من CSRF: فورم من موقع تاني
مايقدرش يبعت application/json من غير CORS preflight.
"""

import os
from functools import wraps

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import current_user, login_required

from models import db, Tenant, User
import provisioning
from provisioning import ProvisionError

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ROLES = ("owner", "staff")


def _super_admin_emails():
    raw = os.environ.get("SUPER_ADMIN_EMAILS", "engeslamelsayad@gmail.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_super_admin(user=None):
    user = user or current_user
    return bool(getattr(user, "is_authenticated", False)
                and (user.email or "").strip().lower() in _super_admin_emails())


def super_admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not is_super_admin():
            abort(404)
        return f(*args, **kwargs)
    return wrapper


def _json_body():
    if not request.is_json:
        abort(415)
    return request.get_json(silent=True) or {}


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _user_dict(u):
    return {
        "id": u.id, "email": u.email, "full_name": u.full_name or "",
        "role": u.role or "owner", "tenant_id": u.tenant_id,
        "tenant_name": u.tenant.business_name if u.tenant else "—",
        "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else "",
        "is_super_admin": is_super_admin(u),
        "is_me": u.id == current_user.id,
    }


@admin_bp.app_context_processor
def _inject_admin_flag():
    # للـ sidebar في base.html — اللينك بيظهر للأدمن بس
    try:
        return {"is_super_admin": is_super_admin()}
    except Exception:
        return {"is_super_admin": False}


# ── الصفحة ────────────────────────────────────────────────────────────
@admin_bp.route("/users")
@super_admin_required
def users_page():
    tenants = Tenant.query.order_by(Tenant.created_at).all()
    users = User.query.order_by(User.created_at).all()
    return render_template(
        "admin_users.html",
        tenant=db.session.get(Tenant, current_user.tenant_id),   # للـ sidebar
        tenants_json=[{"id": t.id, "name": t.business_name + ("" if t.is_active else " (موقوفة)")}
                      for t in tenants],
        users=[_user_dict(u) for u in users],
        roles=ROLES,
        min_password=provisioning.MIN_PASSWORD_LEN,
    )


# ── API: المستخدمين ───────────────────────────────────────────────────
@admin_bp.route("/api/users", methods=["POST"])
@super_admin_required
def api_create_user():
    data = _json_body()
    try:
        user, password = provisioning.create_user(
            tenant_id=data.get("tenant_id"),
            email=data.get("email"),
            password=(data.get("password") or "").strip() or None,
            full_name=data.get("full_name", ""),
            role=data.get("role") if data.get("role") in ROLES else "owner",
        )
        db.session.commit()
    except ProvisionError as e:
        db.session.rollback()
        return _err(str(e))
    print(f"🛠 admin {current_user.email}: أضاف مستخدم {user.email}")
    return jsonify({"ok": True, "user": _user_dict(user), "password": password})


@admin_bp.route("/api/users/<user_id>", methods=["POST"])
@super_admin_required
def api_update_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    data = _json_body()
    try:
        if "email" in data:
            email = provisioning.normalize_email(data["email"])
            if email != user.email:
                provisioning.validate_email(email, exclude_user_id=user.id)
                # ماتقفلش الباب على نفسك: الأدمن مايغيرش إيميله لإيميل مش أدمن
                if user.id == current_user.id and email not in _super_admin_emails():
                    raise ProvisionError("مينفعش تغيّر إيميلك لإيميل مش أدمن — هتفقد الوصول للصفحة دي")
                user.email = email
        if "full_name" in data:
            user.full_name = (data["full_name"] or "").strip()
        if "role" in data:
            if data["role"] not in ROLES:
                raise ProvisionError("دور غير معروف")
            user.role = data["role"]
        if "tenant_id" in data and data["tenant_id"] != user.tenant_id:
            if not db.session.get(Tenant, data["tenant_id"]):
                raise ProvisionError("الشركة مش موجودة")
            user.tenant_id = data["tenant_id"]
        new_password = None
        if data.get("reset_password"):
            new_password = (data.get("new_password") or "").strip() or provisioning.generate_password()
            provisioning.validate_password(new_password)
            user.set_password(new_password)
        db.session.commit()
    except ProvisionError as e:
        db.session.rollback()
        return _err(str(e))
    print(f"🛠 admin {current_user.email}: عدّل المستخدم {user.email}"
          f"{' (باسورد جديد)' if new_password else ''}")
    db.session.refresh(user)
    return jsonify({"ok": True, "user": _user_dict(user), "password": new_password})


@admin_bp.route("/api/users/<user_id>/delete", methods=["POST"])
@super_admin_required
def api_delete_user(user_id):
    _json_body()
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        return _err("مينفعش تحذف حسابك انت")
    email = user.email
    db.session.delete(user)
    db.session.commit()
    print(f"🛠 admin {current_user.email}: حذف المستخدم {email}")
    return jsonify({"ok": True})


# ── API: الشركات ──────────────────────────────────────────────────────
@admin_bp.route("/api/tenants", methods=["POST"])
@super_admin_required
def api_create_tenant():
    data = _json_body()
    try:
        tenant, user, password = provisioning.create_tenant(
            slug=data.get("slug"),
            business_name=data.get("business_name"),
            owner_email=data.get("email"),
            owner_password=(data.get("password") or "").strip() or None,
            bot_name=(data.get("bot_name") or "").strip() or "سارة",
            dialect=(data.get("dialect") or "").strip() or "مصري",
        )
        db.session.commit()
    except ProvisionError as e:
        db.session.rollback()
        return _err(str(e))
    print(f"🛠 admin {current_user.email}: أنشأ شركة {tenant.slug} + {user.email}")
    return jsonify({
        "ok": True, "password": password, "user": _user_dict(user),
        "tenant": {"id": tenant.id, "slug": tenant.slug, "business_name": tenant.business_name},
    })
