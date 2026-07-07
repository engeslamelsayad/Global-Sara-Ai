"""
demo.py — صفحة الديمو الحية (Demo Bot)

صفحة عامة (بدون تسجيل دخول) فيها شات مباشر مع البوت — أقوى أداة مبيعات للـ SaaS:
التاجر المحتمل يكلم البوت ويشوفه بيبيع إزاي قبل ما يشترك.

- بيستخدم tenant الديمو (متغير البيئة DEMO_TENANT_SLUG، الافتراضي "eecm")
- الجلسات معزولة (كل زائر ليه sender_id خاص) وبتستخدم نفس محرك البوت الحقيقي
- حماية التوكنز: حد أقصى 15 رسالة لكل جلسة + طول رسالة 500 حرف
"""

import os
import uuid
import time
from flask import Blueprint, render_template, request, jsonify

demo_bp = Blueprint("demo", __name__, url_prefix="/demo")

DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "eecm")
MAX_DEMO_MESSAGES = 15          # حد أقصى لكل جلسة (حماية التوكنز)
MAX_MESSAGE_LEN = 500

# عدّاد رسائل الجلسات في الذاكرة {session_id: (count, first_ts)}
_session_counts = {}


def _get_demo_bundle():
    """يبني bundle للـ demo tenant (نفس بنية البوت الحقيقي)"""
    from models import Tenant
    from bot_engine import _serialize_tenant_bundle
    tenant = Tenant.query.filter_by(slug=DEMO_TENANT_SLUG, is_active=True).first()
    if not tenant:
        return None
    return _serialize_tenant_bundle(tenant)


@demo_bp.route("/")
def demo_page():
    """صفحة الشات التجريبي"""
    bundle = _get_demo_bundle()
    bot_name = "سارة"
    business_name = "المتجر التجريبي"
    if bundle:
        bc = bundle.get("bot_config")
        if bc and bc.bot_name:
            bot_name = bc.bot_name
        business_name = bundle["tenant"].business_name or business_name
    return render_template("demo.html", bot_name=bot_name,
                           business_name=business_name)


@demo_bp.route("/chat", methods=["POST"])
def demo_chat():
    """معالجة رسالة من الديمو — بيستخدم محرك البوت الحقيقي"""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:MAX_MESSAGE_LEN]
    session_id = (data.get("session") or "").strip()

    if not message:
        return jsonify({"error": "الرسالة فارغة"}), 400
    if not session_id or len(session_id) > 64:
        session_id = uuid.uuid4().hex

    # ── حماية التوكنز: حد أقصى للرسائل لكل جلسة ──
    count, first_ts = _session_counts.get(session_id, (0, time.time()))
    # تنظيف الجلسات الأقدم من ساعتين
    if len(_session_counts) > 2000:
        cutoff = time.time() - 7200
        for k in list(_session_counts.keys()):
            if _session_counts[k][1] < cutoff:
                del _session_counts[k]
    if count >= MAX_DEMO_MESSAGES:
        return jsonify({
            "reply": "وصلت للحد الأقصى للتجربة المجانية 😊 عجبك البوت؟ "
                     "تواصل معانا عشان نفعّله على صفحتك!",
            "limit_reached": True,
            "session": session_id,
        })
    _session_counts[session_id] = (count + 1, first_ts)

    bundle = _get_demo_bundle()
    if not bundle:
        return jsonify({"error": "الديمو غير متاح حالياً"}), 503

    # ── نفس محرك البوت الحقيقي ──
    from bot_engine import (get_ai_response, load_state, save_state,
                            default_state)
    tenant_id = bundle["tenant"].id
    sender_id = f"demo_{session_id}"

    state = load_state(tenant_id, sender_id)
    if not state:
        state = default_state(tenant_id, "demo", "demo")

    try:
        reply, new_order, matched_product = get_ai_response(
            bundle, sender_id, message, state
        )
    except Exception as e:
        print(f"⚠️ Demo chat error: {e}")
        return jsonify({"error": "حصل خطأ، جرّب تاني"}), 500

    # في الديمو مانسجّلش طلبات حقيقية — بس نبيّن إن الطلب "اتسجل"
    save_state(tenant_id, sender_id, state)

    # لو فيه منتج مطابق وليه صورة، نبعت رابط الصورة للعرض في الديمو
    product_image = ""
    if matched_product and (matched_product.image_urls or "").strip():
        key = matched_product.product_key
        if key not in state.get("images_sent", []):
            product_image = matched_product.image_urls.split(",")[0].strip()
            state.setdefault("images_sent", []).append(key)
            save_state(tenant_id, sender_id, state)

    return jsonify({
        "reply": reply,
        "session": session_id,
        "remaining": MAX_DEMO_MESSAGES - (count + 1),
        "order_detected": bool(new_order),
        "product_image": product_image,
    })
