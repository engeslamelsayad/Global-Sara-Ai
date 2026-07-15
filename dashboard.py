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
    FollowupStage, Page, Order, SmartRule, MetaLabel,
)
from auth import login_required_dashboard
from bot_engine import invalidate_tenant_cache
import ai_assist

import json as _json

def _clean_form(value):
    """ينظّف قيمة الفورم — يشيل 'None' اللي اتسجّلت غلط من bug العرض القديم"""
    v = (value or "").strip()
    return "" if v in ("None", "none", "null") else v


def _parse_features(raw):
    """يحوّل نص أسطر لـ JSON list من المميزات"""
    items = [l.strip().lstrip("•-").strip() for l in raw.split("\n") if l.strip()]
    return _json.dumps(items, ensure_ascii=False)

def _parse_faq(raw):
    """يحوّل نص س/ج لـ JSON list — كل سطر س: ... ثم ج: ..."""
    pairs, current_q = [], None
    for line in raw.split("\n"):
        line = line.strip()
        if line.lower().startswith("س:") or line.startswith("سؤال:"):
            current_q = line.split(":", 1)[1].strip()
        elif (line.lower().startswith("ج:") or line.startswith("جواب:")) and current_q:
            pairs.append({"q": current_q, "a": line.split(":", 1)[1].strip()})
            current_q = None
    return _json.dumps(pairs, ensure_ascii=False)


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


# =====================================================================
# STORE IMPORT — استيراد المنتجات من رابط المتجر (حل هجين)
# =====================================================================
@dashboard_bp.route("/products/import")
@login_required_dashboard
def products_import():
    tenant = _current_tenant()
    return render_template("products_import.html", tenant=tenant)


@dashboard_bp.route("/products/import/scan", methods=["POST"])
@login_required_dashboard
def products_import_scan():
    """يمسح رابط المتجر ويرجّع المنتجات المكتشفة للمعاينة"""
    import store_importer
    tenant = _current_tenant()
    url = (request.json.get("url") or "").strip()
    if not url:
        return jsonify({"error": "الرابط فارغ"}), 400

    bc = tenant.bot_config
    dialect = bc.dialect if bc else "مصري"
    result = store_importer.import_store(url, dialect=dialect)

    if result["error"]:
        return jsonify({"error": result["error"]}), 400

    return jsonify({
        "method": result["method"],
        "products": result["products"],
        "count": len(result["products"]),
    })


@dashboard_bp.route("/products/import/easyorders", methods=["POST"])
@login_required_dashboard
def products_import_easyorders():
    """يستورد المنتجات من EasyOrders عبر الـ API Key الرسمي"""
    import store_importer
    api_key = (request.json.get("api_key") or "").strip()
    store_url = (request.json.get("store_url") or "").strip()
    if not api_key:
        return jsonify({"error": "مفتاح الـ API فارغ"}), 400

    result = store_importer.import_from_easyorders_api(api_key, store_url=store_url)
    if result["error"]:
        return jsonify({"error": result["error"]}), 400

    return jsonify({
        "method": "easyorders_api",
        "products": result["products"],
        "count": len(result["products"]),
    })


@dashboard_bp.route("/products/import/save", methods=["POST"])
@login_required_dashboard
def products_import_save():
    """يستورد المنتجات المختارة فعلياً للداتابيز"""
    import re as _re
    tenant = _current_tenant()
    selected = request.json.get("products", [])
    if not selected:
        return jsonify({"error": "لم يتم اختيار أي منتج"}), 400

    # المفاتيح الموجودة عشان منكررش
    existing_keys = {p.product_key for p in Product.query.filter_by(tenant_id=tenant.id).all()}
    existing_names = {p.name.strip().lower() for p in Product.query.filter_by(tenant_id=tenant.id).all()}

    added, skipped = 0, 0
    for item in selected:
        name = (item.get("name") or "").strip()
        if not name or name.lower() in existing_names:
            skipped += 1
            continue

        # نولّد product_key فريد من الاسم
        base_key = _re.sub(r"[^a-z0-9]+", "_", name.lower())[:30].strip("_")
        if not base_key or len(base_key) < 2:
            # الاسم عربي خالص أو قصير — نستخدم رقم تسلسلي
            base_key = f"prod_{len(existing_keys) + 1}"
        key = base_key
        n = 1
        while key in existing_keys:
            key = f"{base_key}_{n}"
            n += 1
        existing_keys.add(key)

        price = item.get("price_amount")
        try:
            price = float(price) if price else None
        except (ValueError, TypeError):
            price = None

        product = Product(
            tenant_id=tenant.id,
            product_key=key,
            name=name,
            description=(item.get("description") or "").strip(),
            keywords=(item.get("keywords") or "").strip(),
            price_type="single",
            price_amount=price,
            shipping_fee=0,
            price_note=(item.get("price_note") or "").strip(),
            product_link=(item.get("product_link") or "").strip(),
            image_urls=(item.get("image_urls") or "").strip(),
            # المحتوى البيعي (لو المنتجات اتأثرت بالـ AI قبل الحفظ)
            # مهم: features و faq لازم يتخزنوا JSON (نفس صيغة النظام)
            features=_parse_features(item.get("features_text") or "") if (item.get("features_text") or "").strip() else None,
            who_benefits=(item.get("who_benefits") or "").strip() or None,
            results_timeline=(item.get("results_timeline") or "").strip() or None,
            closing_pitch=(item.get("closing_pitch") or "").strip() or None,
            faq=_parse_faq(item.get("faq_text") or "") if (item.get("faq_text") or "").strip() else None,
        )
        db.session.add(product)
        added += 1

    db.session.commit()
    _invalidate(tenant)
    return jsonify({"added": added, "skipped": skipped})


@dashboard_bp.route("/products/import/enrich", methods=["POST"])
@login_required_dashboard
def products_import_enrich():
    """
    إثراء المنتجات المستوردة بالمحتوى البيعي بالـ AI قبل الحفظ.
    بياخد دفعات صغيرة (5 منتجات/طلب AI) عشان الجودة والسرعة.
    """
    import ai_assist
    tenant = _current_tenant()
    products = request.json.get("products", [])
    if not products:
        return jsonify({"error": "لا توجد منتجات"}), 400

    bc = tenant.bot_config
    dialect = bc.dialect if bc else "مصري"
    biz_desc = bc.business_description if bc and hasattr(bc, "business_description") else ""

    enriched = []
    failed_batches = 0
    BATCH = 3   # دفعات صغيرة = رد أقصر = مفيش قطع في الـ JSON
    for i in range(0, len(products), BATCH):
        batch = products[i:i + BATCH]
        done = None
        for attempt in (1, 2):   # محاولتين لكل دفعة
            try:
                done = ai_assist.enrich_products_batch(batch, biz_desc, dialect)
                break
            except Exception as e:
                print(f"⚠️ Enrich batch {i//BATCH+1} attempt {attempt} failed: {str(e)[:80]}")
        if done:
            enriched.extend(done)
        else:
            # الدفعة فشلت مرتين — نكمل بالبيانات الأساسية بدل ما نوقف الاستيراد
            failed_batches += 1
            enriched.extend(batch)

    if failed_batches:
        print(f"⚠️ Enrichment: {failed_batches} دفعة فشلت — منتجاتها اتستوردت بدون إثراء")

    return jsonify({"products": enriched, "count": len(enriched)})


@dashboard_bp.route("/products/new", methods=["GET", "POST"])
@login_required_dashboard
def product_new():
    tenant = _current_tenant()
    if request.method == "POST":
        # الحقول الأساسية
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
            image_urls=request.form.get("image_urls", "").strip(),
            sensitive_area_safe=bool(request.form.get("sensitive_area_safe")),
            sensitive_area_note=_clean_form(request.form.get("sensitive_area_note")),
            # الحقول الجديدة
            features=_parse_features(request.form.get("features", "")),
            who_benefits=_clean_form(request.form.get("who_benefits")),
            results_timeline=_clean_form(request.form.get("results_timeline")),
            faq=_parse_faq(request.form.get("faq", "")),
            cross_selling=_clean_form(request.form.get("cross_selling")),
            closing_pitch=_clean_form(request.form.get("closing_pitch")),
        )
        db.session.add(product)
        db.session.commit()
        _invalidate(tenant)
        flash(f"تمت إضافة المنتج '{product.name}' بنجاح ✅", "success")
        return redirect(url_for("dashboard.products_list"))

    return render_template("product_form.html", tenant=tenant, product=None,
                           product_features_text="", product_faq_text="")


@dashboard_bp.route("/products/<product_id>/edit", methods=["GET", "POST"])
@login_required_dashboard
def product_edit(product_id):
    tenant = _current_tenant()
    product = Product.query.filter_by(id=product_id, tenant_id=tenant.id).first_or_404()

    # ── شفاء تلقائي: نمسح نص "None" اللي اتسجّل غلط من bug عرض قديم ──
    _none_fields = ("description", "who_benefits", "results_timeline",
                    "closing_pitch", "cross_selling", "sensitive_area_note",
                    "price_note", "product_link", "image_urls", "keywords")
    _healed = False
    for _f in _none_fields:
        if (getattr(product, _f, None) or "").strip() in ("None", "none", "null"):
            setattr(product, _f, "")
            _healed = True
    if _healed:
        db.session.commit()

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
        product.image_urls    = request.form.get("image_urls", "").strip()
        product.sensitive_area_safe = bool(request.form.get("sensitive_area_safe"))
        product.sensitive_area_note = _clean_form(request.form.get("sensitive_area_note"))
        product.features         = _parse_features(request.form.get("features", ""))
        product.who_benefits     = _clean_form(request.form.get("who_benefits"))
        product.results_timeline = _clean_form(request.form.get("results_timeline"))
        product.faq              = _parse_faq(request.form.get("faq", ""))
        product.cross_selling    = _clean_form(request.form.get("cross_selling"))
        product.closing_pitch    = _clean_form(request.form.get("closing_pitch"))
        product.is_active        = bool(request.form.get("is_active"))
        db.session.commit()
        _invalidate(tenant)
        flash("تم تحديث المنتج بنجاح ✅", "success")
        return redirect(url_for("dashboard.products_list"))

    import json as _j

    def _safe_features_text(raw):
        """يقرأ features سواء JSON list أو نص عادي (منتجات قديمة/مستوردة)"""
        if not raw:
            return ""
        try:
            parsed = _j.loads(raw)
            if isinstance(parsed, list):
                return "\n".join(str(x) for x in parsed)
            return str(parsed)
        except (ValueError, TypeError):
            return raw   # نص عادي بالفعل — نعرضه زي ما هو

    def _safe_faq_text(raw):
        """يقرأ faq سواء JSON [{"q","a"}] أو نص عادي"""
        if not raw:
            return ""
        try:
            parsed = _j.loads(raw)
            if isinstance(parsed, list):
                return "\n".join(
                    f"س: {item.get('q','')}\nج: {item.get('a','')}"
                    for item in parsed if isinstance(item, dict)
                )
            return str(parsed)
        except (ValueError, TypeError):
            return raw   # نص عادي (زي "س: ...\nج: ...") — نعرضه زي ما هو

    feats_text = _safe_features_text(product.features)
    faqs_text  = _safe_faq_text(product.faq)
    return render_template("product_form.html", tenant=tenant, product=product,
                           product_features_text=feats_text, product_faq_text=faqs_text)


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


@dashboard_bp.route("/products/bulk-delete", methods=["POST"])
@login_required_dashboard
def products_bulk_delete():
    """حذف جماعي لمنتجات محددة — آمن ضد حذف منتجات tenant تاني"""
    tenant = _current_tenant()
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    if not ids or not isinstance(ids, list):
        return jsonify({"error": "لم يتم تحديد منتجات"}), 400
    if len(ids) > 500:
        return jsonify({"error": "عدد كبير جداً"}), 400

    # الفلترة بـ tenant_id إجبارية — مايقدرش يحذف منتجات حساب تاني
    deleted = (Product.query
               .filter(Product.tenant_id == tenant.id, Product.id.in_(ids))
               .delete(synchronize_session=False))
    db.session.commit()
    _invalidate(tenant)
    print(f"🗑 Bulk delete: {deleted} منتج ({tenant.slug})")
    return jsonify({"deleted": deleted})


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


@dashboard_bp.route("/products/ai-suggest-url", methods=["POST"])
@login_required_dashboard
def product_ai_suggest_url():
    """يستخرج بيانات المنتج من رابط الـ landing page"""
    tenant = _current_tenant()
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "الرابط فارغ"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    bc = tenant.bot_config
    result = ai_assist.suggest_product_from_url(
        url,
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
        def _cap(field, limit):
            """يقص القيمة للحد الأقصى للعمود — يمنع 500 لو الـ AI اقترح نص طويل"""
            return (request.form.get(field, "") or "").strip()[:limit]

        bc.bot_name    = _cap("bot_name", 80) or bc.bot_name
        bc.bot_age     = int(request.form.get("bot_age") or bc.bot_age)
        bc.bot_persona = request.form.get("bot_persona", "").strip()   # Text — بلا حد
        bc.dialect     = _cap("dialect", 40)
        bc.tone        = _cap("tone", 200)
        bc.max_reply_lines = int(request.form.get("max_reply_lines") or 5)
        bc.use_emojis  = bool(request.form.get("use_emojis"))

        forbidden_words = [w.strip() for w in request.form.get("forbidden_words", "").split(",") if w.strip()]
        bc.forbidden_words = json.dumps(forbidden_words, ensure_ascii=False)

        forbidden_openers = [o.strip() for o in request.form.get("forbidden_openers", "").split("\n") if o.strip()]
        bc.forbidden_openers = json.dumps(forbidden_openers, ensure_ascii=False)

        bc.objection_expensive_response = request.form.get("objection_expensive_response", "").strip()
        bc.objection_unsure_response    = request.form.get("objection_unsure_response", "").strip()
        bc.objection_later_response     = request.form.get("objection_later_response", "").strip()

        bc.contact_number  = _cap("contact_number", 40)
        bc.contact_channel = request.form.get("contact_channel", "whatsapp")
        bc.debounce_seconds = int(request.form.get("debounce_seconds") or 45)
        bc.enable_vision    = bool(request.form.get("enable_vision"))

        # العروض الديناميكية
        bc.offer_hesitation_enabled   = bool(request.form.get("offer_hesitation_enabled"))
        bc.offer_hesitation_threshold = int(request.form.get("offer_hesitation_threshold") or 2)
        bc.offer_hesitation_percent   = int(request.form.get("offer_hesitation_percent") or 10)
        bc.offer_bundle_enabled       = bool(request.form.get("offer_bundle_enabled"))
        bc.offer_bundle_text          = request.form.get("offer_bundle_text", "").strip()

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"⚠️ settings_bot save error: {e}")
            flash("حصل خطأ أثناء الحفظ — جرّب تختصر النصوص الطويلة وحاول تاني", "error")
            return redirect(url_for("dashboard.settings_bot"))
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
    # اللهجة المختارة في الصفحة دلوقتي (حتى لو لسه ماتحفظتش) — وإلا المحفوظة
    dialect = (request.json or {}).get("dialect", "").strip() if request.is_json else ""
    if not dialect:
        dialect = tenant.bot_config.dialect if tenant.bot_config else "مصري"
    try:
        result = ai_assist.suggest_bot_persona(
            tenant.business_description, industry=tenant.industry or "",
            dialect=dialect,
        )
    except Exception as e:
        print(f"⚠️ ai_suggest_persona error: {e}")
        return jsonify({"error": "حصل خطأ في الاتصال بالـ AI — حاول تاني"}), 500
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
    try:
        result = ai_assist.review_business_description(raw)
    except Exception as e:
        print(f"⚠️ ai_review_description error: {e}")
        return jsonify({"error": "حصل خطأ في الاتصال بالـ AI — حاول تاني"}), 500
    return jsonify(result)


@dashboard_bp.route("/settings/general/from-url", methods=["POST"])
@login_required_dashboard
def business_from_url():
    """يقرأ رابط المتجر ويستخرج منه بيانات البزنس تلقائياً"""
    url = (request.json.get("url") or "").strip()
    if not url:
        return jsonify({"error": "اكتب رابط المتجر أولاً"}), 400
    try:
        result = ai_assist.extract_business_from_url(url)
    except Exception as e:
        print(f"⚠️ business_from_url error: {e}")
        return jsonify({"error": "حصل خطأ أثناء قراءة الموقع — حاول تاني"}), 500
    if result.get("error"):
        return jsonify(result), 400
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


@dashboard_bp.route("/keywords/add-json", methods=["POST"])
@login_required_dashboard
def keyword_add_json():
    """
    إضافة كلمة أو أكتر بالـ AJAX — من غير reload للصفحة.
    بيرجّع الكلمات المضافة بالـ IDs بتاعتها عشان الواجهة تحدّث نفسها.
    """
    tenant = _current_tenant()
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    values = data.get("values") or ([data["value"]] if data.get("value") else [])

    if category not in ("human", "complaint"):
        return jsonify({"error": "تصنيف غير صالح"}), 400
    values = [str(v).strip() for v in values if str(v).strip()][:50]
    if not values:
        return jsonify({"error": "اكتب كلمة أولاً"}), 400

    # منع التكرار — الكلمات الموجودة بالفعل بتتخطى
    existing = {k.value for k in Keyword.query.filter_by(
        tenant_id=tenant.id, category=category).all()}
    added = []
    for v in values:
        if v in existing:
            continue
        kw = Keyword(tenant_id=tenant.id, category=category, value=v)
        db.session.add(kw)
        db.session.flush()
        added.append({"id": kw.id, "value": v})
        existing.add(v)

    if added:
        db.session.commit()
        _invalidate(tenant)
    return jsonify({"added": added, "skipped": len(values) - len(added)})


@dashboard_bp.route("/keywords/<keyword_id>/delete-json", methods=["POST"])
@login_required_dashboard
def keyword_delete_json(keyword_id):
    """حذف كلمة بالـ AJAX — من غير reload"""
    tenant = _current_tenant()
    kw = Keyword.query.filter_by(id=keyword_id, tenant_id=tenant.id).first()
    if not kw:
        return jsonify({"error": "الكلمة غير موجودة"}), 404
    db.session.delete(kw)
    db.session.commit()
    _invalidate(tenant)
    return jsonify({"deleted": True})


@dashboard_bp.route("/keywords/ai-suggest", methods=["POST"])
@login_required_dashboard
def keywords_ai_suggest():
    tenant = _current_tenant()
    category = request.json.get("category", "complaint")
    existing = [k.value for k in Keyword.query.filter_by(tenant_id=tenant.id, category=category).all()]
    try:
        result = ai_assist.suggest_keywords_for_category(
            category, business_description=tenant.business_description or "", existing_keywords=existing
        )
    except Exception as e:
        print(f"⚠️ keywords_ai_suggest error: {e}")
        return jsonify({"error": "حصل خطأ في الاتصال بالـ AI — حاول تاني"}), 500
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


# =====================================================================
# SMART RULES — قواعد ذكية مخصصة
# =====================================================================
@dashboard_bp.route("/smart-rules")
@login_required_dashboard
def smart_rules_list():
    tenant = _current_tenant()
    rules = SmartRule.query.filter_by(tenant_id=tenant.id).order_by(SmartRule.created_at).all()
    return render_template("smart_rules.html", tenant=tenant, rules=rules)


@dashboard_bp.route("/smart-rules/add", methods=["POST"])
@login_required_dashboard
def smart_rule_add():
    tenant = _current_tenant()
    rule_text = request.form.get("rule_text", "").strip()
    category  = request.form.get("category", "custom")
    if rule_text:
        db.session.add(SmartRule(
            tenant_id=tenant.id, rule_text=rule_text, category=category
        ))
        db.session.commit()
        _invalidate(tenant)
        flash("تمت إضافة القاعدة ✅", "success")
    return redirect(url_for("dashboard.smart_rules_list"))


@dashboard_bp.route("/smart-rules/<rule_id>/toggle", methods=["POST"])
@login_required_dashboard
def smart_rule_toggle(rule_id):
    tenant = _current_tenant()
    rule = SmartRule.query.filter_by(id=rule_id, tenant_id=tenant.id).first_or_404()
    rule.is_active = not rule.is_active
    db.session.commit()
    _invalidate(tenant)
    return redirect(url_for("dashboard.smart_rules_list"))


@dashboard_bp.route("/smart-rules/<rule_id>/delete", methods=["POST"])
@login_required_dashboard
def smart_rule_delete(rule_id):
    tenant = _current_tenant()
    rule = SmartRule.query.filter_by(id=rule_id, tenant_id=tenant.id).first_or_404()
    db.session.delete(rule)
    db.session.commit()
    _invalidate(tenant)
    flash("تم حذف القاعدة", "success")
    return redirect(url_for("dashboard.smart_rules_list"))


# =====================================================================
# META LABELS — تسمية labels وربطها بحالات المحادثة
# =====================================================================
@dashboard_bp.route("/labels")
@login_required_dashboard
def labels_list():
    tenant = _current_tenant()
    labels = MetaLabel.query.filter_by(tenant_id=tenant.id).order_by(MetaLabel.created_at).all()
    return render_template("labels.html", tenant=tenant, labels=labels)


@dashboard_bp.route("/labels/add", methods=["POST"])
@login_required_dashboard
def label_add():
    tenant = _current_tenant()
    name = request.form.get("name", "").strip()[:120]
    trigger_stage = request.form.get("trigger_stage", "none")
    custom_condition = request.form.get("custom_condition", "").strip()

    if not name:
        flash("اكتب اسم التصنيف أولاً", "error")
        return redirect(url_for("dashboard.labels_list"))
    # الشرط المخصص إجباري لو الحالة "custom"
    if trigger_stage == "custom" and not custom_condition:
        flash("اكتب الشرط اللي البوت يصنّف على أساسه", "error")
        return redirect(url_for("dashboard.labels_list"))

    db.session.add(MetaLabel(
        tenant_id=tenant.id, name=name, trigger_stage=trigger_stage,
        custom_condition=custom_condition or None,
    ))
    db.session.commit()
    _invalidate(tenant)
    flash(f"تمت إضافة تصنيف '{name}' ✅", "success")
    return redirect(url_for("dashboard.labels_list"))


@dashboard_bp.route("/labels/<label_id>/edit", methods=["POST"])
@login_required_dashboard
def label_edit(label_id):
    tenant = _current_tenant()
    label = MetaLabel.query.filter_by(id=label_id, tenant_id=tenant.id).first_or_404()
    label.name = request.form.get("name", "").strip()[:120] or label.name
    label.trigger_stage = request.form.get("trigger_stage", label.trigger_stage)
    if "custom_condition" in request.form:
        label.custom_condition = request.form.get("custom_condition", "").strip() or None
    # لو الاسم اتغير، امسح الـ cache عشان يتعمل label جديدة بالاسم الجديد
    label.meta_label_ids = "{}"
    db.session.commit()
    _invalidate(tenant)
    flash("تم تحديث الـ label ✅", "success")
    return redirect(url_for("dashboard.labels_list"))


@dashboard_bp.route("/labels/<label_id>/toggle", methods=["POST"])
@login_required_dashboard
def label_toggle(label_id):
    tenant = _current_tenant()
    label = MetaLabel.query.filter_by(id=label_id, tenant_id=tenant.id).first_or_404()
    label.is_active = not label.is_active
    db.session.commit()
    _invalidate(tenant)
    return redirect(url_for("dashboard.labels_list"))


@dashboard_bp.route("/labels/<label_id>/delete", methods=["POST"])
@login_required_dashboard
def label_delete(label_id):
    tenant = _current_tenant()
    label = MetaLabel.query.filter_by(id=label_id, tenant_id=tenant.id).first_or_404()
    db.session.delete(label)
    db.session.commit()
    _invalidate(tenant)
    flash("تم حذف الـ label", "success")
    return redirect(url_for("dashboard.labels_list"))


# =====================================================================
# TELEGRAM — ربط تقارير أسبوعية على تليجرام
# =====================================================================
@dashboard_bp.route("/telegram")
@login_required_dashboard
def telegram_settings():
    import os
    tenant = _current_tenant()
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "")
    return render_template("telegram.html", tenant=tenant, bot_username=bot_username)


@dashboard_bp.route("/telegram/generate-code", methods=["POST"])
@login_required_dashboard
def telegram_generate_code():
    import random, string
    tenant = _current_tenant()
    # كود ربط قصير وفريد
    code = "LINK-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    tenant.telegram_link_code = code
    db.session.commit()
    _invalidate(tenant)
    return jsonify({"code": code})


@dashboard_bp.route("/telegram/disconnect", methods=["POST"])
@login_required_dashboard
def telegram_disconnect():
    tenant = _current_tenant()
    tenant.telegram_chat_id = None
    tenant.telegram_enabled = False
    tenant.telegram_link_code = None
    db.session.commit()
    _invalidate(tenant)
    flash("تم فصل تليجرام", "success")
    return redirect(url_for("dashboard.telegram_settings"))


@dashboard_bp.route("/telegram/test", methods=["POST"])
@login_required_dashboard
def telegram_test():
    """يبعت تقرير تجريبي فوراً للتأكد إن الربط شغّال — شامل تحليل الـ AI"""
    import telegram_bot, analytics
    tenant = _current_tenant()
    if not tenant.telegram_enabled or not tenant.telegram_chat_id:
        return jsonify({"ok": False, "error": "لم يتم ربط تليجرام بعد"}), 400
    data = analytics.get_tenant_analytics(tenant)

    # تحليل AI لأسباب فقدان البيع (نفس اللي بيحصل في تقرير السبت)
    loss_analysis = None
    try:
        import ai_assist
        from scheduler import _collect_lost_samples
        samples = _collect_lost_samples(tenant)
        if samples:
            bc = tenant.bot_config
            loss_analysis = ai_assist.analyze_lost_conversations(
                samples, bc.dialect if bc else "مصري")
    except Exception as e:
        print(f"⚠️ AI analysis in test report failed: {e}")

    report = telegram_bot.build_weekly_report(tenant, data, loss_analysis=loss_analysis)
    ok = telegram_bot.send_message(tenant.telegram_chat_id, report)
    return jsonify({"ok": ok})


# =====================================================================
# PROFILE — مفتاح التحليلات
# =====================================================================
@dashboard_bp.route("/profile", methods=["GET", "POST"])
@login_required_dashboard
def profile():
    from flask_login import current_user
    from models import User
    user = User.query.get(current_user.id)
    tenant = _current_tenant()

    if request.method == "POST":
        user.analytics_key = request.form.get("analytics_key", "").strip()
        db.session.commit()
        flash("تم حفظ مفتاح التحليلات ✅", "success")
        return redirect(url_for("dashboard.profile"))

    return render_template("profile.html", tenant=tenant, user=user)
