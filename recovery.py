"""
recovery.py — استرجاع السلة المهجورة الذكي (Smart Recovery)

بدل رسالة متابعة عامة، البوت بيحلّل **سبب توقف العميل** ويبعت رسالة مخصصة:
- اعترض على السعر (OBJECTION) → رسالة إنقاذ + خصم المرحلة
- كان مهتم (INTERESTED) → تذكير بالمنتج اللي سأل عنه بالاسم
- استفسر وسكت (INQUIRY) → رسالة ودّية تفتح الكلام تاني

بيستخدم مراحل FollowupStage الموجودة (التوقيت + الخصم + النص الأساسي)
ويبني عليها التخصيص الذكي.

بيشتغل من الـ scheduler كل 15 دقيقة.
"""

import time

# حد أقصى للرسائل في الدورة الواحدة لكل tenant (حماية من الانفجار بعد downtime)
MAX_SENDS_PER_RUN = 30


def _pick_product(state, products):
    """يرجّع آخر منتج سأل عنه العميل (لو موجود)"""
    asked = state.get("products_asked", [])
    key = state.get("source_ad_product_key") or (asked[-1] if asked else None)
    if not key:
        return None
    return next((p for p in products if p.product_key == key), None)


def _build_smart_message(stage_row, state, product):
    """
    يبني رسالة المتابعة المخصصة حسب سبب التوقف.
    بيدعم placeholders في نص المرحلة: {product} و {discount}
    """
    base = (stage_row.message_text or "").strip()
    discount = stage_row.discount_percent or 0
    reason = state.get("stage", "")
    pname = product.name if product else ""

    # استبدال الـ placeholders لو التاجر مستخدمها
    msg = base.replace("{product}", pname).replace("{discount}", str(discount))

    # التخصيص الذكي حسب السبب — لو النص الأساسي مافيهوش تخصيص
    if reason == "OBJECTION":
        # عميل اعترض (غالباً السعر) → رسالة إنقاذ
        prefix = ""
        if pname:
            prefix = f"لسه فاكراك يا فندم 😊 بخصوص «{pname}» — "
        if discount > 0:
            offer = (f"{prefix}عشان خاطرك بس، قدرت أوفرلك خصم {discount}% "
                     f"لو أكدت طلبك النهاردة. ده أحسن عرض أقدر أقدمه ليك 🎁")
        else:
            offer = (f"{prefix}فكرت تاني في الموضوع؟ لو السعر هو اللي مقلقك، "
                     f"افتكر إن الدفع عند الاستلام — يعني مفيش أي مخاطرة عليك.")
        # لو التاجر كاتب نص مخصص، نستخدمه؛ وإلا الذكي
        return msg if "{" not in base and len(base) > 20 else offer

    if reason in ("INTERESTED", "INQUIRY") and pname:
        smart = (f"أهلاً بيك تاني يا فندم 👋 كنت بسأل عن «{pname}» — "
                 f"لسه محتاج أي معلومة عنه؟ أنا موجودة أجاوبك على أي سؤال 😊")
        if discount > 0:
            smart += f"\nوعندي ليك مفاجأة: خصم {discount}% لو طلبت النهاردة 🎁"
        return msg if len(base) > 20 and "{" not in base else smart

    # الافتراضي: نص المرحلة زي ما هو (أو رسالة عامة لو فاضي)
    return msg or "أهلاً يا فندم 👋 لسه موجودة لو محتاج أي مساعدة 😊"


def run_followups(app):
    """
    الدورة الرئيسية — بتلف على كل الـ tenants وتبعت المتابعات المستحقة.
    """
    from models import Tenant, FollowupStage
    from bot_engine import (list_tenant_states_with_ids, save_state,
                            get_tenant_for_page, send_message)

    now = time.time()
    total_sent = 0

    with app.app_context():
        tenants = Tenant.query.filter_by(is_active=True).all()
        for tenant in tenants:
            # المتابعات مفعّلة؟
            policy = getattr(tenant, "policy", None)
            if policy and not getattr(policy, "enable_followup", True):
                continue

            stages = (FollowupStage.query
                      .filter_by(tenant_id=tenant.id, is_active=True)
                      .order_by(FollowupStage.stage_number).all())
            if not stages:
                continue

            sends_this_tenant = 0
            for sender_id, state in list_tenant_states_with_ids(tenant.id):
                if sends_this_tenant >= MAX_SENDS_PER_RUN:
                    break
                # شروط الاستبعاد
                if (state.get("has_order") or state.get("is_human_handoff")
                        or state.get("has_complaint")
                        or state.get("platform") == "demo"
                        or not state.get("page_id")
                        or state.get("page_id") == "demo"
                        or not state.get("history")):
                    continue

                last_msg = state.get("last_message") or state.get("created_at") or 0
                if not last_msg:
                    continue

                sent_stages = state.get("followup_stages_sent", [])
                # المرجع الزمني: آخر تفاعل (رسالة العميل أو آخر متابعة اتبعتت)
                # عشان المراحل ماتتبعتش ورا بعض في نفس الدورة
                last_ref = max(last_msg, state.get("last_followup_time") or 0)
                # أول مرحلة مستحقة لم تُرسل بعد
                for st in stages:
                    if st.stage_number in sent_stages:
                        continue
                    hours_passed = (now - last_ref) / 3600
                    if hours_passed < st.hours_after_last:
                        break   # المراحل مرتبة — اللي بعدها أبعد

                    # بناء الرسالة الذكية وإرسالها
                    bundle = get_tenant_for_page(state["page_id"])
                    if not bundle:
                        break
                    product = _pick_product(state, bundle["products"])
                    msg = _build_smart_message(st, state, product)

                    send_message(bundle, sender_id, msg,
                                 state["page_id"], state.get("platform", "facebook"))

                    state.setdefault("followup_stages_sent", []).append(st.stage_number)
                    state["last_followup_time"] = now
                    if st.discount_percent:
                        state["has_discount"] = st.discount_percent
                    save_state(tenant.id, sender_id, state)

                    reason = state.get("stage", "?")
                    print(f"🔔 Smart recovery [{reason}] stage {st.stage_number} "
                          f"→ {sender_id[:12]}... ({tenant.slug})")
                    sends_this_tenant += 1
                    total_sent += 1
                    break   # مرحلة واحدة لكل عميل في الدورة

    if total_sent:
        print(f"✅ Smart recovery: {total_sent} رسالة متابعة اتبعتت")
