"""
bot_engine.py — المحرك الأساسي للبوت متعدد الشركات (Multi-tenant)

كل دالة هنا بتاخد tenant ككائن، وكل سلوك البوت (الشخصية، اللهجة، الـ debounce،
كشف الموديريتور، الـ follow-up، معالجة الاعتراضات) بيتبني ديناميكياً من بيانات
الـ tenant في الداتابيز — مفيش أي حاجة مكتوبة ثابتة في الكود.

الاستخدام المتوقع من webhook.py:
    tenant = get_tenant_for_page(page_id)
    buffer_message(tenant, sender_id, text, page_id, platform, image_b64=...)
"""

import os
import re
import json
import time
import threading
from datetime import datetime

import anthropic
import requests

from models import db, Tenant, BotConfig, Policy, Product, Keyword, BotAppId, Order

# =====================================================================
# CLIENTS
# =====================================================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

ORDER_PATTERN = re.compile(
    r"\[ORDER\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|?([^\]]*)\]",
    re.IGNORECASE
)

# =====================================================================
# STATE — Redis مع fallback لذاكرة محلية (نفس نمط النسخة القديمة)
# =====================================================================
REDIS_URL = os.environ.get("REDIS_URL", "")
_redis_client = None
_memory_meta   = {}     # fallback لو مفيش Redis (تطوير محلي فقط)
_memory_lock   = threading.Lock()


def get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL:
        return None
    try:
        import redis as _redis
        _redis_client = _redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=3)
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        print(f"⚠️ Redis connect error: {e}")
        return None


def _state_key(tenant_id, sender_id):
    return f"conv:{tenant_id}:{sender_id}"


def load_state(tenant_id, sender_id):
    r = get_redis()
    key = _state_key(tenant_id, sender_id)
    if r:
        try:
            raw = r.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            print(f"⚠️ Redis load error: {e}")
    with _memory_lock:
        return _memory_meta.get(key, {}).copy()


def save_state(tenant_id, sender_id, state):
    r = get_redis()
    key = _state_key(tenant_id, sender_id)
    if r:
        try:
            r.set(key, json.dumps(state, ensure_ascii=False), ex=2592000)   # 30 يوم
            return
        except Exception as e:
            print(f"⚠️ Redis save error: {e}")
    with _memory_lock:
        _memory_meta[key] = state.copy()


def list_tenant_states(tenant_id):
    """يرجع كل حالات محادثات tenant معين — للاستخدام في حساب الـ analytics"""
    prefix = f"conv:{tenant_id}:"
    r = get_redis()
    if r:
        try:
            states = []
            for key in r.scan_iter(match=f"{prefix}*"):
                raw = r.get(key)
                if raw:
                    states.append(json.loads(raw))
            return states
        except Exception as e:
            print(f"⚠️ Redis scan error: {e}")
            return []
    with _memory_lock:
        return [v.copy() for k, v in _memory_meta.items() if k.startswith(prefix)]


def default_state(tenant_id, page_id, platform):
    now = time.time()
    return {
        "tenant_id": tenant_id, "page_id": page_id, "platform": platform,
        "stage": "NEW", "first_contact": now, "last_message": now,
        "products_asked": [], "links_sent": [],
        "has_order": False, "has_complaint": False, "is_human_handoff": False,
        "followup_stages_sent": [],      # [1, 2, ...] أرقام المراحل اللي اتبعتت
        "last_followup_time": None,
        "has_discount": 0,               # نسبة الخصم لو اتبعتت
        "messages_count": 0,
        "history": [],                   # [{"role":..,"content":..}, ...] آخر N رسالة
    }


# =====================================================================
# TENANT LOOKUP (مع كاش بسيط في الذاكرة لتقليل ضغط الداتابيز)
# =====================================================================
_tenant_cache = {}          # {page_id: (tenant_full_dict, timestamp)}
_TENANT_CACHE_TTL = 60       # ثانية


def _serialize_tenant_bundle(tenant):
    """يجمع كل بيانات الـ tenant اللازمة للبوت في dict واحد قابل للكاش"""
    bot_config = tenant.bot_config
    policy     = tenant.policy
    products   = [p for p in tenant.products if p.is_active]
    keywords   = tenant.keywords
    app_ids    = {a.app_id for a in tenant.bot_app_ids}
    pages      = {p.page_id: p for p in tenant.pages if p.is_active}

    return {
        "tenant": tenant, "bot_config": bot_config, "policy": policy,
        "products": products, "keywords": keywords,
        "bot_app_ids": app_ids, "pages": pages,
    }


def get_tenant_for_page(page_id):
    """يرجع bundle كامل لبيانات الـ tenant المرتبط بصفحة معينة، مع كاش 60 ثانية"""
    cached = _tenant_cache.get(page_id)
    if cached and (time.time() - cached[1]) < _TENANT_CACHE_TTL:
        return cached[0]

    from models import Page
    page = Page.query.filter_by(page_id=page_id, is_active=True).first()
    if not page:
        return None

    tenant = Tenant.query.get(page.tenant_id)
    if not tenant or not tenant.is_active:
        return None

    bundle = _serialize_tenant_bundle(tenant)
    _tenant_cache[page_id] = (bundle, time.time())
    return bundle


def invalidate_tenant_cache(page_id=None):
    """تُستدعى من الداشبورد بعد أي تعديل عشان الكاش يتحدّث فوراً"""
    if page_id:
        _tenant_cache.pop(page_id, None)
    else:
        _tenant_cache.clear()


# =====================================================================
# RAG — البحث عن المنتج المناسب من رسالة العميل
# =====================================================================
def find_relevant_product(message, products):
    """بحث بسيط بالكلمات المفتاحية — يرجع أول منتج متطابق أو None"""
    msg_lower = message.lower()
    best_match, best_score = None, 0

    for product in products:
        score = 0
        kw_list = [k.strip() for k in (product.keywords or "").split(",") if k.strip()]
        for kw in kw_list:
            if kw.lower() in msg_lower:
                score += 1
        if product.name and any(word in msg_lower for word in product.name.split()):
            score += 1
        if score > best_score:
            best_score, best_match = score, product

    return best_match


# =====================================================================
# SYSTEM PROMPT BUILDER — ديناميكي بالكامل من بيانات الـ tenant
# =====================================================================
def build_system_prompt(bundle, matched_product=None, state=None):
    """
    يبني الـ system prompt كـ list من blocks لدعم الـ prompt caching:
      block 1 (static, cached): شخصية البوت + قواعد + السياسات + كل المنتجات
      block 2 (dynamic): المنتج المطابق للرسالة الحالية + تنبيهات السياق
    """
    tenant     = bundle["tenant"]
    bc         = bundle["bot_config"]
    policy     = bundle["policy"]
    products   = bundle["products"]

    forbidden_words   = json.loads(bc.forbidden_words or "[]")
    forbidden_openers = json.loads(bc.forbidden_openers or "[]")

    products_count = len(products)
    products_block = "\n".join(
        f"- {p.name}: {p.price_note or f'{p.price_amount} ج'}"
        for p in products
    ) or "لا توجد منتجات مُضافة بعد"

    forbidden_words_txt = "، ".join(forbidden_words) if forbidden_words else "لا يوجد"
    forbidden_openers_txt = "\n".join(f'- "{o}"' for o in forbidden_openers) or "لا يوجد"

    from models import SmartRule
    active_rules = SmartRule.query.filter_by(
        tenant_id=tenant.id, is_active=True
    ).order_by(SmartRule.created_at).all()

    smart_rules_txt = ""
    if active_rules:
        rules_by_cat = {}
        for r in active_rules:
            rules_by_cat.setdefault(r.category, []).append(r.rule_text)
        smart_rules_txt = "\n[قواعد ذكية مخصصة — التزمي بها]\n"
        for cat, rules in rules_by_cat.items():
            for rule in rules:
                smart_rules_txt += f"- {rule}\n"

    static_prompt = f"""أنت {bc.bot_name}، {bc.bot_persona}.
عمرك {bc.bot_age} سنة. بتتكلم بلهجة {bc.dialect} بنبرة {bc.tone}.

[اسم الشركة]
{tenant.business_name}
{tenant.business_description or ""}

[كلمات ممنوعة — لا تستخدميها أبداً]
{forbidden_words_txt}

[افتتاحيات ممنوعة — لا تبدأي ردك بأي منها]
{forbidden_openers_txt}

[أسلوب الرد]
- ردود قصيرة ومركزة: حد أقصى {bc.max_reply_lines} أسطر
- كل رد ينتهي بسؤال أو دعوة للشراء
- لا تستخدمي * أو ** أو markdown
{"- يمكنك استخدام إيموجي بشكل معتدل" if bc.use_emojis else "- ممنوع استخدام أي إيموجي"}

[سياسات الشركة الثابتة]
- طريقة الدفع: {policy.payment_method if policy else "غير محدد"}
- مدة التوصيل: {policy.delivery_days if policy else "غير محدد"}
- الاستبدال: {policy.exchange_policy if policy else "غير محدد"}
- الاسترجاع: {policy.return_policy if policy else "غير محدد"}
- المعاينة: {policy.inspection_policy if policy else "غير محدد"}
{"- لا يوجد نظام تقسيط — لا تذكريه أبداً" if policy and not policy.enable_installments else ""}

[معالجة الاعتراضات]
"غالي": {bc.objection_expensive_response or "اشرحي القيمة مقابل السعر"}
"مش متأكد": {bc.objection_unsure_response or "اطمنيه بالضمان"}
"هفكر": {bc.objection_later_response or "أكدي محدودية الكمية"}

[بيانات التواصل]
رقم {('الواتساب' if bc.contact_channel == 'whatsapp' else 'التواصل')} الرسمي: {bc.contact_number or 'غير محدد'}
{"هذا الرقم للواتساب فقط — مش للمكالمات" if bc.contact_channel == "whatsapp" else ""}
لا تذكري أي رقم آخر أبداً تحت أي ظرف

{smart_rules_txt}
[⛔ ممنوع رص كل المنتجات]
لو العميل سأل سؤال عام زي "عندكم إيه؟" أو "بتبيعوا إيه؟" أو "عايز المنتج ده" بدون تحديد مشكلته:
❌ ممنوع ترصّي قائمة المنتجات ({products_count} منتج) أو أي جزء منها
✅ بدل كده قولي إن عندنا {products_count} منتج لمشاكل مختلفة، واسأليه عن مشكلته:
   مثال: "عندنا {products_count} منتج طبيعي لمشاكل مختلفة 😊 قوليلي إيه اللي بيواجهك وأرشحلك المناسب"
لما يذكر مشكلته المحددة → وقتها بس ترشحي المنتج المناسب. تعاملي كبائع شاطر مش كتالوج.

[قائمة المنتجات — للرجوع الداخلي فقط، مش للعرض على العميل]
{products_block}

[تسجيل الطلب]
لما تتأكدي من الاسم والموبايل والعنوان والمنتج، حطي في آخر ردك:
[ORDER|الاسم|الموبايل|العنوان|المنتج]
لو العميل عنده خصم من رسالة متابعة سابقة، أضيفي |DISCOUNT{{نسبة}} في الآخر.
هذا السطر للنظام فقط، لا يظهر للعميل.

[⛔⛔ قاعدة حرجة — لا تكرري طلب بيانات موجودة]
راجعي المحادثة كلها قبل ما تطلبي أي بيانات:
- لو العميل بعت اسمه قبل كده → احفظيه، ممنوع تطلبيه تاني
- لو بعت رقم موبايله → ممنوع تطلبيه تاني
- لو بعت عنوانه → ممنوع تطلبيه تاني
- لو حدد المنتج → ممنوع تسأليه عنه تاني
اطلبي البيانات الناقصة فقط. لو عندك كل البيانات → سجلي الطلب فوراً بـ [ORDER|...] من غير ما تطلبي حاجة تاني.
تكرار طلب البيانات بيزعّل العميل ويخسرنا الأوردر — أسوأ خطأ ممكن تعمليه.

[قاعدة صارمة — لا تكرري تسجيل الطلب]
لو سبق وقلتِ "طلبك اتسجل" في هذه المحادثة، لا تكتبي [ORDER|...] مرة أخرى أبداً.
"""

    dynamic_parts = []

    if matched_product:
        import json as _json
        features_list  = _json.loads(matched_product.features or "[]")
        faq_list       = _json.loads(matched_product.faq or "[]")
        features_txt   = "\n".join(f"  • {f}" for f in features_list) if features_list else ""
        faq_txt        = "\n".join(f"  س: {item['q']}\n  ج: {item['a']}" for item in faq_list) if faq_list else ""

        prod_block = (
            f"⚠️ المنتج المقصود في رسالة العميل الحالية:\n"
            f"الاسم: {matched_product.name}\n"
            f"الوصف: {matched_product.description or ''}\n"
            f"السعر الدقيق: {matched_product.price_note} — استخدمي هذا السعر فقط.\n"
        )
        if features_txt:
            prod_block += f"المميزات الرئيسية:\n{features_txt}\n"
        if matched_product.who_benefits:
            prod_block += f"من يستفيد: {matched_product.who_benefits}\n"
        if matched_product.results_timeline:
            prod_block += f"متى تظهر النتيجة: {matched_product.results_timeline}\n"
        if matched_product.closing_pitch:
            prod_block += f"نص إغلاق البيع: {matched_product.closing_pitch}\n"
        if faq_txt:
            prod_block += f"أسئلة شائعة:\n{faq_txt}\n"
        if matched_product.cross_selling:
            cross_names = []
            for ck in matched_product.cross_selling.split(","):
                ck = ck.strip()
                cp = next((p for p in products if p.product_key == ck), None)
                if cp:
                    cross_names.append(cp.name)
            if cross_names:
                prod_block += f"منتجات مكملة يمكن اقتراحها: {', '.join(cross_names)}\n"

        dynamic_parts.append(prod_block)

        if policy and policy.enable_sensitive_area_warning:
            if matched_product.sensitive_area_safe:
                dynamic_parts.append(
                    f"ملاحظة أمان: هذا المنتج آمن لاستخدام خاص — {matched_product.sensitive_area_note}"
                )
            else:
                dynamic_parts.append(
                    "تذكير: هذا المنتج للجسم بشكل عام — قبل تأكيد الطلب اسألي العميل "
                    "'المنطقة المتأثرة فين بالظبط؟' (سؤال مفتوح بدون أمثلة). "
                    "لو ذكر وجه/جفن/إبط/منطقة تناسلية أو شرجية: ارفضي البيع بأدب وانصحي بطبيب."
                )
    else:
        dynamic_parts.append("لم يتم تحديد منتج بعد — اسألي العميل عن مشكلته أولاً.")

    if policy and policy.enable_chronic_disease_warning:
        dynamic_parts.append(
            "لو العميل ذكر مرض مزمن (قلب/سكر/ضغط/كلى/كبد/حمل/رضاعة): "
            "انصحيه يستشير طبيبه قبل تأكيد الطلب."
        )

    if state and state.get("has_order"):
        dynamic_parts.append(
            "⛔ تنبيه: هذا العميل سجّل طلبه بالفعل. لا تكتبي [ORDER|...] مرة أخرى."
        )

    if state and state.get("has_discount"):
        dynamic_parts.append(
            f"هذا العميل لديه خصم {state['has_discount']}% من رسالة متابعة سابقة — "
            f"أضيفي |DISCOUNT{state['has_discount']} عند تسجيل الطلب."
        )

    dynamic_prompt = "\n\n".join(dynamic_parts)

    return [
        {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic_prompt},
    ]


# =====================================================================
# KEYWORD MATCHING
# =====================================================================
def matches_category(message, keywords, category):
    msg_lower = message.lower()
    return any(
        kw.value.lower() in msg_lower
        for kw in keywords if kw.category == category
    )


def get_closing_reactions(bc):
    try:
        return set(json.loads(bc.closing_reactions or "[]"))
    except Exception:
        return {"👍", "✅", "🙏", "👌"}


# =====================================================================
# AI RESPONSE
# =====================================================================
def get_ai_response(bundle, sender_id, user_message, state,
                    image_b64=None, image_media_type="image/jpeg"):
    bc       = bundle["bot_config"]
    products = bundle["products"]
    keywords = bundle["keywords"]

    matched_product = find_relevant_product(user_message, products)
    system_blocks   = build_system_prompt(bundle, matched_product, state)

    history = state.get("history", [])
    if image_b64 and bc.enable_vision:
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": image_b64}},
            {"type": "text", "text": user_message or "شوف الصورة دي وقوليلي إيه المشكلة"},
        ]
    else:
        user_content = user_message

    messages = history + [{"role": "user", "content": user_content}]

    response_obj = client.messages.create(
        model=bc.model_name or "claude-sonnet-4-6",
        max_tokens=bc.max_tokens or 600,
        system=system_blocks,
        messages=messages,
    )
    response_text = response_obj.content[0].text

    # ── كشف الطلب ──
    order_match = ORDER_PATTERN.search(response_text)
    new_order = None
    if order_match:
        response_text = ORDER_PATTERN.sub("", response_text).strip()
        if not state.get("has_order"):
            groups = order_match.groups()
            new_order = {
                "name": groups[0].strip(), "phone": groups[1].strip(),
                "address": groups[2].strip(), "product": groups[3].strip(),
                "discount": groups[4].strip() if len(groups) > 4 else "",
            }
            state["has_order"] = True
            state["stage"] = "ORDERED"

    # ── تحديث الـ stage ──
    if matched_product:
        pk = matched_product.product_key
        if pk not in state["products_asked"]:
            state["products_asked"].append(pk)
        if state["stage"] in ("NEW", "INQUIRY"):
            state["stage"] = "INTERESTED"
    elif state["stage"] == "NEW":
        state["stage"] = "INQUIRY"

    if matches_category(user_message, keywords, "objection_expensive") or \
       matches_category(user_message, keywords, "objection_unsure") or \
       matches_category(user_message, keywords, "objection_later"):
        state["stage"] = "OBJECTION"

    response_text = response_text.replace("**", "").replace("*", "")

    hist_text = f"[صورة] {user_message}".strip() if image_b64 else user_message
    state["history"] = (history + [
        {"role": "user", "content": hist_text},
        {"role": "assistant", "content": response_text},
    ])[-16:]

    return response_text, new_order, matched_product


# =====================================================================
# ORDER SAVE
# =====================================================================
def push_to_google_sheet(tenant, order_data, page_id):
    """يبعت بيانات الطلب لـ Google Apps Script Web App الخاص بالـ tenant (غير محجوب — async)"""
    sheet_url = getattr(tenant, "google_sheet_url", None)
    if not sheet_url:
        return
    try:
        requests.post(sheet_url, json={
            "name": order_data["name"], "phone": order_data["phone"],
            "address": order_data["address"], "product": order_data["product"],
            "discount": order_data.get("discount", ""), "page": page_id,
            "business": tenant.business_name,
        }, timeout=10)
        print(f"📊 Order pushed to Google Sheet for tenant {tenant.slug}")
    except Exception as e:
        print(f"⚠️ Google Sheet push error: {e}")


def push_to_google_sheet_async(tenant, order_data, page_id):
    threading.Thread(
        target=push_to_google_sheet, args=(tenant, order_data, page_id), daemon=True
    ).start()


def save_order(tenant, order_data, page_id):
    """tenant: كائن Tenant كامل (مش id فقط) — محتاجينه عشان رابط الـ Google Sheet"""
    order = Order(
        tenant_id=tenant.id,
        customer_name=order_data["name"], customer_phone=order_data["phone"],
        customer_address=order_data["address"], product_name=order_data["product"],
        discount_code=order_data.get("discount", ""), page_id=page_id,
    )
    db.session.add(order)
    db.session.commit()

    # رفع للـ Google Sheet في الخلفية — لا يؤخر رد العميل
    push_to_google_sheet_async(tenant, order_data, page_id)

    return order


# =====================================================================
# MESSAGE SENDING (Facebook/Instagram)
# =====================================================================
def send_message(bundle, sender_id, text, page_id, platform):
    page = bundle["pages"].get(page_id)
    if not page or not page.access_token:
        print(f"❌ No access token for page {page_id}")
        return
    try:
        requests.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": page.access_token},
            json={"recipient": {"id": sender_id}, "message": {"text": text}},
            timeout=10,
        )
    except Exception as e:
        print(f"❌ Send message error: {e}")


def download_meta_image(image_url, access_token):
    try:
        r = requests.get(image_url, params={"access_token": access_token}, timeout=15)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if ct not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            ct = "image/jpeg"
        import base64
        return base64.b64encode(r.content).decode(), ct
    except Exception as e:
        print(f"⚠️ Image download failed: {e}")
        return None, None


# =====================================================================
# META CUSTOM LABELS — تطبيق labels على العملاء حسب حالة المحادثة
# =====================================================================
def _ensure_label_id(label, page_id, access_token):
    """
    يتأكد إن الـ label موجودة على Meta لهذه الصفحة، وبيرجّع الـ meta label id.
    بيكاش الـ id في عمود meta_label_ids (JSON per page) لتفادي إعادة الإنشاء.
    """
    from models import db as _db
    ids = json.loads(label.meta_label_ids or "{}")
    if page_id in ids:
        return ids[page_id]

    # 1) دوّر على label بنفس الاسم على Meta (اتعملت قبل كده)
    try:
        r = requests.get(
            "https://graph.facebook.com/v18.0/me/custom_labels",
            params={"fields": "id,name", "access_token": access_token},
            timeout=10,
        )
        resp = r.json()
        if "error" in resp:
            err = resp["error"]
            print(f"❌ Labels GET error (page {page_id}): code={err.get('code')} — {err.get('message')}")
            if err.get("code") in (10, 200, 190):
                print("   ⚠️ الـ token غالباً ناقص صلاحية pages_manage_metadata")
            return None
        for item in resp.get("data", []):
            if item.get("name") == label.name:
                ids[page_id] = item["id"]
                label.meta_label_ids = json.dumps(ids, ensure_ascii=False)
                _db.session.commit()
                return item["id"]
    except Exception as e:
        print(f"⚠️ Labels lookup error: {e}")
        return None

    # 2) اعمل الـ label من جديد
    try:
        cr = requests.post(
            "https://graph.facebook.com/v18.0/me/custom_labels",
            params={"access_token": access_token},
            json={"name": label.name},
            timeout=10,
        )
        cj = cr.json()
        if "id" in cj:
            ids[page_id] = cj["id"]
            label.meta_label_ids = json.dumps(ids, ensure_ascii=False)
            _db.session.commit()
            print(f"   ➕ label جديدة على Meta: {label.name} ({cj['id']})")
            return cj["id"]
        elif "error" in cj:
            print(f"❌ فشل إنشاء label '{label.name}': {cj['error'].get('message')}")
    except Exception as e:
        print(f"⚠️ Label create error: {e}")
    return None


def _apply_label_to_user(label, sender_id, page_id, access_token):
    label_id = _ensure_label_id(label, page_id, access_token)
    if not label_id:
        return
    try:
        r = requests.post(
            f"https://graph.facebook.com/v18.0/{label_id}/label",
            params={"access_token": access_token},
            json={"user": sender_id},
            timeout=10,
        )
        if r.status_code == 200:
            print(f"🏷️  '{label.name}' → {sender_id} ✅")
        else:
            err = r.json().get("error", {})
            print(f"❌ تطبيق label '{label.name}' فشل: status={r.status_code} code={err.get('code')} — {err.get('message')}")
    except Exception as e:
        print(f"⚠️ Label apply error: {e}")


def apply_stage_labels(bundle, sender_id, page_id, stage):
    """
    يطبّق كل الـ labels المرتبطة بحالة معينة (trigger_stage) — non-blocking.
    stage: interested / objection / ordered / complaint / human_needed
    """
    from models import MetaLabel
    from flask import current_app
    tenant = bundle["tenant"]
    page = bundle["pages"].get(page_id)
    if not page or not page.access_token:
        return

    labels = MetaLabel.query.filter_by(
        tenant_id=tenant.id, trigger_stage=stage, is_active=True
    ).all()
    if not labels:
        return

    label_ids = [l.id for l in labels]
    access_token = page.access_token
    app_obj = current_app._get_current_object()

    def _worker():
        # الـ thread محتاج app context خاص بيه للـ DB operations
        with app_obj.app_context():
            for lid in label_ids:
                lbl = MetaLabel.query.get(lid)
                if lbl:
                    _apply_label_to_user(lbl, sender_id, page_id, access_token)

    threading.Thread(target=_worker, daemon=True).start()


# =====================================================================
# MAIN PROCESSING — تُستدعى بعد انتهاء الـ debounce
# =====================================================================
def do_process_message(tenant_id, sender_id, user_message, page_id, platform,
                       image_b64=None, image_media_type="image/jpeg"):
    bundle = _tenant_cache.get(page_id, (None,))[0]
    if not bundle:
        bundle = get_tenant_for_page(page_id)
    if not bundle:
        print(f"❌ No tenant found for page {page_id}")
        return

    keywords = bundle["keywords"]

    state = load_state(tenant_id, sender_id)
    if not state:
        state = default_state(tenant_id, page_id, platform)

    state["last_message"]    = time.time()
    state["messages_count"]  = state.get("messages_count", 0) + 1
    state["page_id"]         = page_id
    state["platform"]        = platform

    if state.get("is_human_handoff"):
        print(f"🙋 {sender_id} is in human-handoff mode — bot stays silent")
        save_state(tenant_id, sender_id, state)
        return

    if matches_category(user_message, keywords, "human"):
        state["is_human_handoff"] = True
        state["stage"] = "HUMAN_NEEDED"
        send_message(bundle, sender_id,
            "تمام! هبعتلك موظف متخصص دلوقتي. لحظة صغيرة ومحدش هيسيبك وحدك 💙",
            page_id, platform)
        apply_stage_labels(bundle, sender_id, page_id, "human_needed")
        save_state(tenant_id, sender_id, state)
        return

    reply, new_order, matched_product = get_ai_response(
        bundle, sender_id, user_message, state,
        image_b64=image_b64, image_media_type=image_media_type
    )
    send_message(bundle, sender_id, reply, page_id, platform)

    if new_order:
        save_order(bundle["tenant"], new_order, page_id)
        print(f"✅ Order saved for tenant {tenant_id}: {new_order['product']}")
        apply_stage_labels(bundle, sender_id, page_id, "ordered")

    if matched_product and matched_product.product_link:
        if matched_product.product_key not in state.get("links_sent", []):
            time.sleep(0.6)
            send_message(bundle, sender_id,
                f"تقدر/ي تشوف المنتج بالتفصيل هنا 👇\n{matched_product.product_link}",
                page_id, platform)
            state.setdefault("links_sent", []).append(matched_product.product_key)

    # ── labels حسب الحالة ──
    if state.get("stage") == "INTERESTED":
        apply_stage_labels(bundle, sender_id, page_id, "interested")
    elif state.get("stage") == "OBJECTION":
        apply_stage_labels(bundle, sender_id, page_id, "objection")

    is_complaint = matches_category(user_message, keywords, "complaint")
    if is_complaint and not state.get("has_complaint"):
        state["has_complaint"] = True
        state["stage"] = "COMPLAINT"
        print(f"🚨 Complaint detected for {sender_id}")
        apply_stage_labels(bundle, sender_id, page_id, "complaint")

    save_state(tenant_id, sender_id, state)
    print(f"✅ Done — tenant={tenant_id[:8]} stage={state['stage']}")


# =====================================================================
# DEBOUNCING — تجميع الرسائل المتتالية (مدة قابلة للتحكم لكل tenant)
# =====================================================================
pending_messages = {}     # {(tenant_id, sender_id): {...}}
pending_lock = threading.Lock()


def _debounce_fire(tenant_id, sender_id):
    with pending_lock:
        key = (tenant_id, sender_id)
        batch = pending_messages.pop(key, None)
    if not batch:
        return

    combined = "\n".join(batch["messages"])
    if len(batch["messages"]) > 1:
        print(f"📦 Batched {len(batch['messages'])} msgs from {sender_id}")

    do_process_message(
        tenant_id, sender_id, combined,
        batch["page_id"], batch["platform"],
        batch.get("image_b64"), batch.get("image_media_type", "image/jpeg"),
    )


def buffer_message(bundle, sender_id, message, page_id, platform,
                   image_b64=None, image_media_type="image/jpeg"):
    """يضيف رسالة لطابور الانتظار ويبدأ/يجدد مؤقت الـ debounce الخاص بالـ tenant"""
    tenant_id = bundle["tenant"].id
    debounce_seconds = bundle["bot_config"].debounce_seconds or 45
    key = (tenant_id, sender_id)

    with pending_lock:
        if key in pending_messages:
            pending_messages[key]["timer"].cancel()
            pending_messages[key]["messages"].append(message)
            if image_b64 and not pending_messages[key].get("image_b64"):
                pending_messages[key]["image_b64"] = image_b64
                pending_messages[key]["image_media_type"] = image_media_type
        else:
            pending_messages[key] = {
                "messages": [message], "page_id": page_id, "platform": platform,
                "image_b64": image_b64, "image_media_type": image_media_type,
            }

        timer = threading.Timer(debounce_seconds, _debounce_fire, args=[tenant_id, sender_id])
        pending_messages[key]["timer"] = timer
        timer.start()


# =====================================================================
# ECHO DETECTION — كشف رد الموديريتور البشري ووقف البوت فوراً
# =====================================================================
def handle_echo(bundle, event):
    """
    يُستدعى لما الـ webhook event يكون is_echo=True
    لو الـ app_id مش في قائمة bot_app_ids بتاعة الـ tenant → موديريتور بشري رد
    → نوقف البوت لهذا العميل فوراً عشان منعش يحصل تداخل في الكلام
    """
    tenant_id = bundle["tenant"].id
    echo_app_id = str(event["message"].get("app_id", "0"))
    bot_app_ids = bundle["bot_app_ids"]

    if echo_app_id in bot_app_ids:
        return   # ده رد البوت نفسه — تجاهل عادي

    user_psid = event["recipient"]["id"]
    state = load_state(tenant_id, user_psid)
    if not state:
        return
    state["is_human_handoff"] = True
    save_state(tenant_id, user_psid, state)
    print(f"🙋 Moderator echo (app={echo_app_id}) → bot paused for {user_psid}")


# =====================================================================
# CLOSING REACTIONS — تجاهل الإيماءات الختامية (👍 لوحدها)
# =====================================================================
def is_closing_reaction(message, bundle):
    bc = bundle["bot_config"]
    reactions = get_closing_reactions(bc)
    stripped = message.strip()
    if stripped in reactions:
        return True
    if len(stripped) <= 2 and stripped and all(ord(c) > 127 for c in stripped):
        return True
    return False
