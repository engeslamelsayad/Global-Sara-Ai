"""
dashboard.py — كل صفحات الداشبورد اللي مالك البزنس بيشتغل عليها

كل route محمي بـ @login_required_dashboard وبيشتغل على بيانات
tenant واحد بس (current_user.tenant_id) — عزل كامل بين الشركات.

أي تعديل بيمسح كاش bot_engine الخاص بصفحات الـ tenant ده عشان
البوت ياخد التحديث فوراً بدون انتظار.
"""

import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from models import (
    db, Tenant, Product, Policy, BotConfig, Keyword, BotAppId,
    FollowupStage, Page, Order,
)
from auth import login_required_dashboard
from bot_engine import invalidate_tenant_cache
import ai_assist

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _current_tenant():
    return Tenant.query.get(current_user.tenant_id)


def _invalidate(tenant):
    """امسح كاش كل صفحات الـ tenant ده عشان البوت ياخد آخر تحديث فوراً"""
    for page in tenant.pages:
        invalidate_tenant_cache(page.page_id)


# =====================================================================
# HOME
# =====================================================================
@dashboard_bp.route("/")
@login_required_dashboard
def home():
    tenant = _current_tenant()
    products_count = Product.query.filter_by(tenant_id=tenant.id, is_active=True).count()
    orders_count   = Order.query.filter_by(tenant_id=tenant.id).count()
    pages_count    = Page.query.filter_by(tenant_id=tenant.id, is_active=True).count()
    return render_template("dashboard_home.html",
        tenant=tenant, products_count=products_count,
        orders_count=orders_count, pages_count=pages_count)


# =====================================================================
# PRODUCTS
# =====================================================================
@dashboard_bp.route("/products")
@login_required_dashboard
def products_list():
    tenant = _current_tenant()
    products = Product.query.filter_by(tenant_id=tenant.id).order_by(Product.created_at.desc()).all()
    return render_template("products_list.html", tenant=tenant, products=products)


@dashboard_bp.route("/products/new", methods=["GET", "POST"])
@login_required_dashboard
def product_new():
    tenant = _current_tenant()
    if request.method == "POST":
        product = Product(
            tenant_id=tenant.id,
            product_key=request.form["product_key"].strip(),
            name=request.form["name"].strip(),
            description=request.form.get("description", "").strip(),
            keywords=request.form.get("keywords", "").strip(),
            price_type=request.form.get("price_type", "single"),
            price_amount=request.form.get("price_amount") or None,
            shipping_fee=request.form.get("shipping_fee") or 0,
            price_note=request.form.get("price_note", "").strip(),
            product_link=request.form.get("product_link", "").strip(),
            sensitive_area_safe=bool(request.form.get("sensitive_area_safe")),
            sensitive_area_note=request.form.get("sensitive_area_note", "").strip(),
        )
        db.session.add(product)
        db.session.commit()
        _invalidate(tenant)
        flash(f"تمت إضافة المنتج '{product.name}' بنجاح ✅", "success")
        return redirect(url_for("dashboard.products_list"))

    return render_template("product_form.html", tenant=tenant, product=None)


@dashboard_bp.route("/products/<product_id>/edit", methods=["GET", "POST"])
@login_required_dashboard
def product_edit(product_id):
    tenant = _current_tenant()
    product = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first_or_404()

    if request.method == "POST":
        product.product_key   = request.form["product_key"].strip()
        product.name          = request.form["name"].strip()
        product.description   = request.form.get("description", "").strip()
        product.keywords      = request.form.get("keywords", "").strip()
        product.price_type    = request.form.get("price_type", "single")
        product.price_amount  = request.form.get("price_amount") or None
        product.shipping_fee  = request.form.get("shipping_fee") or 0
        product.price_note    = request.form.get("price_note", "").strip()
        product.product_link  = request.form.get("product_link", "").strip()
        product.sensitive_area_safe = bool(request.form.get("sensitive_area_safe"))
        product.sensitive_area_note = request.form.get("sensitive_area_note", "").strip()
        product.is_active     = bool(request.form.get("is_active"))
        db.session.commit()
        _invalidate(tenant)
        flash("تم تحديث المنتج بنجاح ✅", "success")
        return redirect(url_for("dashboard.products_list"))

    return render_template("product_form.html", tenant=tenant, product=product)


@dashboard_bp.route("/products/<product_id>/delete", methods=["POST"])
@login_required_dashboard
def product_delete(product_id):
    tenant = _current_tenant()
    product = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first_or_404()
    db.session.delete(product)
    db.session.commit()
    _invalidate(tenant)
    flash("تم حذف المنتج", "success")
    return redirect(url_for("dashboard.products_list"))


@dashboard_bp.route("/products/ai-suggest", methods=["POST"])
@login_required_dashboard
def product_ai_suggest():
    tenant = _current_tenant()
    short_input = request.json.get("short_input", "")
    if not short_input:
        return jsonify({"error": "اكتب وصف مبدئي للمنتج أولاً"}), 400

    bc = tenant.bot_config
    result = ai_assist.suggest_product_details(
        short_input,
        business_description=tenant.business_description or "",
        dialect=bc.dialect if bc else "مصري",
    )
    return jsonify(result)


# =====================================================================
# BOT SETTINGS
# =====================================================================
@dashboard_bp.route("/settings/bot", methods=["GET", "POST"])
@login_required_dashboard
def settings_bot():
    tenant = _current_tenant()
    bc = tenant.bot_config

    if request.method == "POST":
        bc.bot_name    = request.form.get("bot_name", "").strip() or bc.bot_name
        bc.bot_age     = int(request.form.get("bot_age") or bc.bot_age)
        bc.bot_persona = request.form.get("bot_persona", "").strip()
        bc.dialect     = request.form.get("dialect", "").strip()
        bc.tone        = request.form.get("tone", "").strip()
        bc.max_reply_lines = int(request.form.get("max_reply_lines") or 5)
        bc.use_emojis  = bool(request.form.get("use_emojis"))

        forbidden_words = [w.strip() for w in request.form.get("forbidden_words", "").split(",") if w.strip()]
        bc.forbidden_words = json.dumps(forbidden_words, ensure_ascii=False)

        forbidden_openers = [o.strip() for o in request.form.get("forbidden_openers", "").split("\n") if o.strip()]
        bc.forbidden_openers = json.dumps(forbidden_openers, ensure_ascii=False)

        bc.objection_expensive_response = request.form.get("objection_expensive_response", "").strip()
        bc.objection_unsure_response    = request.form.get("objection_unsure_response", "").strip()
        bc.objection_later_response     = request.form.get("objection_later_response", "").strip()

        bc.contact_number  = request.form.get("contact_number", "").strip()
        bc.contact_channel = request.form.get("contact_channel", "whatsapp")
        bc.debounce_seconds = int(request.form.get("debounce_seconds") or 45)
        bc.enable_vision    = bool(request.form.get("enable_vision"))

        db.session.commit()
        _invalidate(tenant)
        flash("تم تحديث إعدادات البوت ✅", "success")
        return redirect(url_for("dashboard.settings_bot"))

    return render_template("settings_bot.html", tenant=tenant, bc=bc,
        forbidden_words=", ".join(json.loads(bc.forbidden_words or "[]")),
        forbidden_openers="\n".join(json.loads(bc.forbidden_openers or "[]")))


@dashboard_bp.route("/settings/bot/ai-suggest-persona", methods=["POST"])
@login_required_dashboard
def ai_suggest_persona():
    tenant = _current_tenant()
    if not tenant.business_description:
        return jsonify({"error": "اكتب وصف البزنس في صفحة الإعدادات العامة أولاً"}), 400
    result = ai_assist.suggest_bot_persona(
        tenant.business_description, industry=tenant.industry or "",
        dialect=tenant.bot_config.dialect if tenant.bot_config else "مصري",
    )
    return jsonify(result)


@dashboard_bp.route("/settings/bot/ai-suggest-objections", methods=["POST"])
@login_required_dashboard
def ai_suggest_objections():
    tenant = _current_tenant()
    sample_product = Product.query.filter_by(tenant_id=tenant.id).first()
    result = ai_assist.suggest_objection_responses(
        tenant.business_description or "",
        sample_product_name=sample_product.name if sample_product else "",
        dialect=tenant.bot_config.dialect if tenant.bot_config else "مصري",
    )
    return jsonify(result)


# =====================================================================
# GENERAL SETTINGS
# =====================================================================
@dashboard_bp.route("/settings/general", methods=["GET", "POST"])
@login_required_dashboard
def settings_general():
    tenant = _current_tenant()

    if request.method == "POST":
        tenant.business_name        = request.form.get("business_name", "").strip()
        tenant.business_description = request.form.get("business_description", "").strip()
        tenant.industry              = request.form.get("industry", "").strip()
        tenant.google_sheet_url      = request.form.get("google_sheet_url", "").strip()
        db.session.commit()
        _invalidate(tenant)
        flash("تم تحديث البيانات العامة ✅", "success")
        return redirect(url_for("dashboard.settings_general"))

    return render_template("settings_general.html", tenant=tenant)


@dashboard_bp.route("/settings/general/ai-review", methods=["POST"])
@login_required_dashboard
def ai_review_description():
    raw = request.json.get("description", "")
    if not raw:
        return jsonify({"error": "اكتب وصف أولاً"}), 400
    result = ai_assist.review_business_description(raw)
    return jsonify(result)


# =====================================================================
# POLICY SETTINGS
# =====================================================================
@dashboard_bp.route("/settings/policy", methods=["GET", "POST"])
@login_required_dashboard
def settings_policy():
    tenant = _current_tenant()
    policy = tenant.policy

    if request.method == "POST":
        policy.payment_method  = request.form.get("payment_method", "").strip()
        policy.delivery_days   = request.form.get("delivery_days", "").strip()
        policy.return_policy    = request.form.get("return_policy", "").strip()
        policy.exchange_policy  = request.form.get("exchange_policy", "").strip()
        policy.inspection_policy = request.form.get("inspection_policy", "").strip()
        policy.enable_sensitive_area_warning  = bool(request.form.get("enable_sensitive_area_warning"))
        policy.enable_chronic_disease_warning = bool(request.form.get("enable_chronic_disease_warning"))
        policy.enable_followup  = bool(request.form.get("enable_followup"))
        policy.enable_installments = bool(request.form.get("enable_installments"))
        db.session.commit()
        _invalidate(tenant)
        flash("تم تحديث السياسات ✅", "success")
        return redirect(url_for("dashboard.settings_policy"))

    return render_template("settings_policy.html", tenant=tenant, policy=policy)


# =====================================================================
# KEYWORDS
# =====================================================================
@dashboard_bp.route("/keywords")
@login_required_dashboard
def keywords_list():
    tenant = _current_tenant()
    human_kw     = Keyword.query.filter_by(tenant_id=tenant.id, category="human").all()
    complaint_kw = Keyword.query.filter_by(tenant_id=tenant.id, category="complaint").all()
    return render_template("keywords.html", tenant=tenant,
        human_kw=human_kw, complaint_kw=complaint_kw)


@dashboard_bp.route("/keywords/add", methods=["POST"])
@login_required_dashboard
def keyword_add():
    tenant = _current_tenant()
    category = request.form.get("category")
    value    = request.form.get("value", "").strip()
    if value and category in ("human", "complaint"):
        db.session.add(Keyword(tenant_id=tenant.id, category=category, value=value))
        db.session.commit()
        _invalidate(tenant)
        flash("تمت الإضافة ✅", "success")
    return redirect(url_for("dashboard.keywords_list"))


@dashboard_bp.route("/keywords/<keyword_id>/delete", methods=["POST"])
@login_required_dashboard
def keyword_delete(keyword_id):
    tenant = _current_tenant()
    kw = Keyword.query.filter_by(id=keyword_id, tenant_id=tenant.id).first_or_404()
    db.session.delete(kw)
    db.session.commit()
    _invalidate(tenant)
    return redirect(url_for("dashboard.keywords_list"))


@dashboard_bp.route("/keywords/ai-suggest", methods=["POST"])
@login_required_dashboard
def keywords_ai_suggest():
    tenant = _current_tenant()
    category = request.json.get("category", "complaint")
    existing = [k.value for k in Keyword.query.filter_by(tenant_id=tenant.id, category=category).all()]
    result = ai_assist.suggest_keywords_for_category(
        category, business_description=tenant.business_description or "", existing_keywords=existing
    )
    return jsonify(result)


# =====================================================================
# FOLLOWUP STAGES
# =====================================================================
@dashboard_bp.route("/followups")
@login_required_dashboard
def followups_list():
    tenant = _current_tenant()
    stages = FollowupStage.query.filter_by(tenant_id=tenant.id).order_by(FollowupStage.stage_number).all()
    return render_template("followups.html", tenant=tenant, stages=stages)


@dashboard_bp.route("/followups/add", methods=["POST"])
@login_required_dashboard
def followup_add():
    tenant = _current_tenant()
    max_stage = db.session.query(db.func.max(FollowupStage.stage_number)) \
        .filter_by(tenant_id=tenant.id).scalar() or 0

    stage = FollowupStage(
        tenant_id=tenant.id,
        stage_number=max_stage + 1,
        hours_after_last=int(request.form.get("hours_after_last", 24)),
        message_text=request.form.get("message_text", "").strip(),
        discount_percent=int(request.form.get("discount_percent", 0)),
    )
    db.session.add(stage)
    db.session.commit()
    _invalidate(tenant)
    flash(f"تمت إضافة المرحلة #{stage.stage_number} ✅", "success")
    return redirect(url_for("dashboard.followups_list"))


@dashboard_bp.route("/followups/<stage_id>/delete", methods=["POST"])
@login_required_dashboard
def followup_delete(stage_id):
    tenant = _current_tenant()
    stage = FollowupStage.query.filter_by(id=stage_id, tenant_id=tenant.id).first_or_404()
    db.session.delete(stage)
    db.session.commit()
    _invalidate(tenant)
    return redirect(url_for("dashboard.followups_list"))


@dashboard_bp.route("/followups/<stage_id>/toggle", methods=["POST"])
@login_required_dashboard
def followup_toggle(stage_id):
    tenant = _current_tenant()
    stage = FollowupStage.query.filter_by(id=stage_id, tenant_id=tenant.id).first_or_404()
    stage.is_active = not stage.is_active
    db.session.commit()
    _invalidate(tenant)
    return redirect(url_for("dashboard.followups_list"))


# =====================================================================
# PAGES
# =====================================================================
@dashboard_bp.route("/pages")
@login_required_dashboard
def pages_list():
    tenant = _current_tenant()
    pages = Page.query.filter_by(tenant_id=tenant.id).all()
    return render_template("pages.html", tenant=tenant, pages=pages)


@dashboard_bp.route("/pages/add", methods=["POST"])
@login_required_dashboard
def page_add():
    tenant = _current_tenant()
    page = Page(
        tenant_id=tenant.id,
        platform=request.form.get("platform", "page"),
        page_id=request.form.get("page_id", "").strip(),
        label=request.form.get("label", "").strip(),
        access_token=request.form.get("access_token", "").strip(),
    )
    db.session.add(page)
    db.session.commit()
    flash("تمت إضافة الصفحة ✅", "success")
    return redirect(url_for("dashboard.pages_list"))


@dashboard_bp.route("/pages/<page_id>/delete", methods=["POST"])
@login_required_dashboard
def page_delete(page_id):
    tenant = _current_tenant()
    page = Page.query.filter_by(id=page_id, tenant_id=tenant.id).first_or_404()
    invalidate_tenant_cache(page.page_id)
    db.session.delete(page)
    db.session.commit()
    flash("تم حذف الصفحة", "success")
    return redirect(url_for("dashboard.pages_list"))


# =====================================================================
# ORDERS
# =====================================================================
@dashboard_bp.route("/orders")
@login_required_dashboard
def orders_list():
    tenant = _current_tenant()
    orders = Order.query.filter_by(tenant_id=tenant.id).order_by(Order.created_at.desc()).limit(100).all()
    return render_template("orders.html", tenant=tenant, orders=orders)
