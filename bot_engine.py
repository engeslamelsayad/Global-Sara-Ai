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
        cached = _memory_meta.get(key)
        return json.loads(json.dumps(cached, ensure_ascii=False)) if cached else {}


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
        _memory_meta[key] = json.loads(json.dumps(state, ensure_ascii=False))


def list_tenant_states(tenant_id):
    """يرجع كل حالات محادثات tenant معين — للاستخدام في حساب الـ analytics"""
    return [s for _, s in list_tenant_states_with_ids(tenant_id)]


def list_tenant_states_with_ids(tenant_id):
    """يرجع [(sender_id, state), ...] — للمتابعات اللي محتاجة تبعت رسائل"""
    prefix = f"conv:{tenant_id}:"
    r = get_redis()
    if r:
        try:
            out = []
            for key in r.scan_iter(match=f"{prefix}*"):
                raw = r.get(key)
                if raw:
                    k = key.decode() if isinstance(key, bytes) else key
                    out.append((k[len(prefix):], json.loads(raw)))
            return out
        except Exception as e:
            print(f"⚠️ Redis scan error: {e}")
            return []
    with _memory_lock:
        return [(k[len(prefix):], json.loads(json.dumps(v, ensure_ascii=False)))
                for k, v in _memory_meta.items() if k.startswith(prefix)]


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
def _normalize_ar(text):
    """تطبيع النص العربي: توحيد الهمزات والألف والياء والتاء المربوطة"""
    if not text:
        return ""
    text = text.lower().strip()
    # توحيد الألف بأشكالها
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    # توحيد الياء والألف المقصورة
    text = text.replace("ى", "ي")
    # توحيد التاء المربوطة والهاء
    text = text.replace("ة", "ه")
    # إزالة التشكيل
    for d in "ًٌٍَُِّْ":
        text = text.replace(d, "")
    return text


def _is_strong_match(message, product):
    """
    يتأكد إن الرسالة فيها كلمة مفتاحية كاملة للمنتج (مطابقة قوية)،
    مش مجرد جزء كلمة عام زي 'الجلدية' اللي ممكن تطابق منتجات كتير.
    """
    if not product or not product.keywords:
        return False
    msg_norm = _normalize_ar(message)
    for kw in product.keywords.split(","):
        kw_norm = _normalize_ar(kw)
        # كلمة مفتاحية مميزة (أطول من 4 حروف) وموجودة كاملة = مطابقة قوية
        if len(kw_norm) >= 4 and kw_norm in msg_norm:
            return True
    return False


def find_relevant_product(message, products):
    """بحث بسيط بالكلمات المفتاحية — يرجع أول منتج متطابق أو None"""
    msg_norm = _normalize_ar(message)
    best_match, best_score = None, 0

    for product in products:
        score = 0
        kw_list = [k.strip() for k in (product.keywords or "").split(",") if k.strip()]
        for kw in kw_list:
            if _normalize_ar(kw) in msg_norm:
                score += 1
        if product.name:
            for word in product.name.split():
                if len(word) >= 3 and _normalize_ar(word) in msg_norm:
                    score += 1
        if score > best_score:
            best_score, best_match = score, product

    return best_match


def capture_ad_referral(bundle, event, sender_id):
    """
    يلتقط إعلان المصدر (Click-to-Messenger) من الـ referral ويخزّن المنتج المطابق
    في حالة المحادثة — عشان لو العميل سأل 'بكام؟' يعرف البوت المنتج.
    """
    # الـ referral ممكن يكون جوه message أو event مستقل
    referral = (event.get("message", {}) or {}).get("referral") or event.get("referral")
    if not referral or referral.get("source") != "ADS":
        return
    ad_title = referral.get("ads_context_data", {}).get("ad_title", "")
    if not ad_title:
        return

    tenant = bundle["tenant"]
    matched = find_relevant_product(ad_title, bundle["products"])
    state = load_state(tenant.id, sender_id)
    state["source_ad_title"] = ad_title
    if matched:
        state["source_ad_product_key"] = matched.product_key
        print(f"📢 عميل من إعلان: '{ad_title[:40]}' → منتج: {matched.name}")
    else:
        print(f"📢 عميل من إعلان: '{ad_title[:40]}' (لم يُطابَق منتج)")
    save_state(tenant.id, sender_id, state)


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

[⛔ قاعدة اللهجة]
التزمي تماماً بلهجة {bc.dialect} في كل كلمة من ردودك — من أول رسالة لآخر رسالة.
ممنوع تخلطي لهجات (مثلاً كلمات شامية في رد مصري، أو مصرية في رد خليجي).
لو اللهجة "سعودي" أو "إماراتي" أو "خليجي": استخدمي مفردات خليجية أصيلة
(وش، كيف، أبغى/أبا، الحين، مره، زين، يعطيك العافية) وتجنّبي المصرية (إيه، إزاي، دلوقتي، أوي).
لو اللهجة "مصري": استخدمي المصرية الطبيعية (إيه، إزاي، دلوقتي، خالص، أوي)
وتجنّبي الشامية (هلق، شو، هيك، منيح).

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

[⛔ قاعدة الشحن — مهمة جداً لا تخالفيها]
مصاريف الشحن ممكن تختلف من منتج لمنتج — فيه منتجات شحنها مجاني وشامل في السعر.
❌ ممنوع تقولي رقم شحن ثابت (زي "الشحن 50 جنيه") قبل ما تعرفي المنتج المحدد
❌ ممنوع تعمّمي رقم شحن واحد على كل المنتجات
✅ لو العميل سأل عن الشحن قبل ما يحدد المنتج، قوليله:
   "مصاريف الشحن بتختلف حسب المنتج يا فندم — فيه منتجات شحنها مجاني 😊
    قوليلي المشكلة اللي بتواجهك وأنا أقولك السعر النهائي شامل كل حاجة لحد البيت"
✅ لو العميل جه من إعلان منتج معيّن، استخدمي سعر وشحن المنتج ده مباشرة
✅ لما تعرفي المنتج، قولي السعر النهائي شامل الشحن (مش الشحن لوحده)
الهدف: ماتوقفيش البيعة برقم شحن غلط. دايماً السعر النهائي شامل.

[📷 لو العميل بعت صورة]
- لو الصورة لمشكلة صحية/جلدية (حبوب، إكزيما، زوائد، فطريات...): حلليها بلطف واهتمام،
  واوصفي اللي شايفاه بشكل عام (من غير تشخيص طبي جازم)، ورشّحي المنتج الأنسب من
  قائمة المنتجات مع سعره النهائي. مثال: "شايفة إن فيه احمرار وتهيج واضح — ده بالظبط
  اللي [المنتج] بيعالجه، وسعره X ج شامل التوصيل"
- لو الصورة لمنتج من منتجاتنا: أكدي إنه متوفر واذكري سعره وميزته الأساسية
- لو الصورة مش واضحة أو مش متعلقة: اسألي بلطف عن المشكلة اللي بيواجهها
- ممنوع تدّعي إنك دكتورة أو تدي تشخيص طبي قاطع — انتي بترشّحي منتج مناسب بس

[معالجة الاعتراضات]
"غالي": {bc.objection_expensive_response or "اشرحي القيمة مقابل السعر"}
"مش متأكد": {bc.objection_unsure_response or "اطمنيه بالضمان"}
"هفكر": {bc.objection_later_response or "أكدي محدودية الكمية"}

[بيانات التواصل]
رقم {('الواتساب' if bc.contact_channel == 'whatsapp' else 'التواصل')} الرسمي: {bc.contact_number or 'غير محدد'}
{"هذا الرقم للواتساب فقط — مش للمكالمات" if bc.contact_channel == "whatsapp" else ""}
لا تذكري أي رقم آخر أبداً تحت أي ظرف

[⛔ قاعدة الاستبدال والاسترجاع — مهمة جداً]
لو العميل طلب استبدال أو استرجاع أو مرتجع أو استرداد فلوس:
❌ ممنوع تسجّلي طلب استرجاع أو تاخدي بياناته لتسجيل مرتجع
❌ ممنوع تقولي "استرجاع كامل" أو تحددي أي مبلغ هيرجعله
❌ ممنوع تعملي [ORDER|...] لطلب استرجاع
✅ طمّنيه إن حقه محفوظ ووجّهيه للتواصل على الرقم الرسمي:
   "حقك محفوظ تماماً يا فندم وكل عملائنا مقدّرين عندنا 💙 الاستبدال أو الاسترجاع بيتم
    من خلال التواصل على {('الواتساب' if bc.contact_channel == 'whatsapp' else 'الرقم')}: {bc.contact_number or 'الرقم الرسمي'} — كلّمنا هناك ونظبّطلك كل حاجة"
السبب: تفاصيل الاسترجاع (زي خصم الشحن) بتتحدد مع فريق خاص، مش البوت. خليكي متعاطفة بلا وعود برقم معيّن.

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

        def _safe_list(raw):
            """يقرأ JSON list أو نص عادي بأسطر (منتجات مستوردة) بدون كسر"""
            if not raw:
                return []
            try:
                parsed = _json.loads(raw)
                return parsed if isinstance(parsed, list) else [str(parsed)]
            except (ValueError, TypeError):
                return [l.strip() for l in str(raw).split("\n") if l.strip()]

        features_list  = _safe_list(matched_product.features)
        raw_faq        = _safe_list(matched_product.faq)
        features_txt   = "\n".join(f"  • {f}" for f in features_list) if features_list else ""
        # الـ faq ممكن يكون [{"q","a"}] أو أسطر نص "س: .. ج: .."
        faq_lines = []
        for item in raw_faq:
            if isinstance(item, dict):
                faq_lines.append(f"  س: {item.get('q','')}\n  ج: {item.get('a','')}")
            else:
                faq_lines.append(f"  {item}")
        faq_txt = "\n".join(faq_lines) if faq_lines else ""

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

    # ── العروض الديناميكية ──
    if state and bc:
        # 1) خصم التردد: العميل اعترض أكتر من مرة → اعرضي خصم لإنقاذ البيعة
        if (getattr(bc, "offer_hesitation_enabled", False)
                and state.get("objections_count", 0) >= (getattr(bc, "offer_hesitation_threshold", 2) or 2)
                and not state.get("dynamic_offer_used")
                and not state.get("has_order")):
            pct = getattr(bc, "offer_hesitation_percent", 10) or 10
            dynamic_parts.append(
                f"🎯 عرض إنقاذ البيعة: العميل متردد جداً (اعترض {state['objections_count']} مرات). "
                f"اعرضي عليه الآن خصم {pct}% كعرض خاص لفترة محدودة — قوليها بحماس وكأنه عرض استثنائي ليه. "
                f"لو وافق وسجّل الطلب، أضيفي |DISCOUNT{pct} في نهاية سطر [ORDER|...]."
            )
        # 2) عرض الـ bundle: العميل سأل عن منتجين → اعرضي ياخدهم مع بعض
        if (getattr(bc, "offer_bundle_enabled", False)
                and len(state.get("products_asked", [])) >= 2
                and not state.get("bundle_offer_used")
                and not state.get("has_order")):
            bundle_txt = (getattr(bc, "offer_bundle_text", "") or
                          "لو خدتهم مع بعض في نفس الطلب، الشحن مرة واحدة بس — توفير حقيقي!")
            dynamic_parts.append(
                f"🎁 عرض الباقة: العميل مهتم بأكتر من منتج ({len(state['products_asked'])} منتجات). "
                f"اقترحي عليه ياخدهم مع بعض: \"{bundle_txt}\""
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

    # ── عميل جاي من إعلان: فضّل منتج الإعلان في بداية المحادثة ──
    # ده بيحل مشكلتين:
    # 1. "بكام؟" من غير اسم منتج → يستخدم منتج الإعلان
    # 2. رسالة فيها خطأ إملائي طابقت منتج غلط → منتج الإعلان أدق
    ad_key = state.get("source_ad_product_key")
    if ad_key and not state.get("links_sent"):
        # لسه في بداية المحادثة (مبعتناش link) والعميل جه من إعلان
        ad_product = next((p for p in products if p.product_key == ad_key), None)
        if ad_product:
            # لو مفيش مطابقة، أو المطابقة مش هي منتج الإعلان لكن ضعيفة → فضّل الإعلان
            if not matched_product or not _is_strong_match(user_message, matched_product):
                matched_product = ad_product
                print(f"🎯 تفضيل منتج الإعلان: {ad_product.name}")

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
        state["objections_count"] = state.get("objections_count", 0) + 1

    # العروض الديناميكية: لو الشروط تحققت في الرد ده، نعلّم إن العرض اتقدّم
    # (عشان مايتكررش في كل رسالة)
    if bc:
        if (getattr(bc, "offer_hesitation_enabled", False)
                and state.get("objections_count", 0) >= (getattr(bc, "offer_hesitation_threshold", 2) or 2)
                and not state.get("dynamic_offer_used")):
            state["dynamic_offer_used"] = True
            print(f"🎯 Dynamic hesitation offer triggered for {sender_id}")
        if (getattr(bc, "offer_bundle_enabled", False)
                and len(state.get("products_asked", [])) >= 2
                and not state.get("bundle_offer_used")):
            state["bundle_offer_used"] = True
            print(f"🎁 Bundle offer triggered for {sender_id}")

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


def _telegram_alert(tenant, text):
    """
    تنبيه تليجرام فوري للتاجر (لو مربوط) — للأحداث الحرجة:
    طلب جديد، عميل غاضب، طلب تدخل بشري.
    Fire-and-forget: أي فشل مايأثرش على flow البوت.
    """
    try:
        if not getattr(tenant, "telegram_enabled", False):
            return
        chat_id = getattr(tenant, "telegram_chat_id", None)
        if not chat_id:
            return
        import telegram_bot
        threading.Thread(
            target=telegram_bot.send_message,
            args=(chat_id, text),
            daemon=True,
        ).start()
    except Exception as e:
        print(f"⚠️ Telegram alert error: {e}")


def send_image(bundle, sender_id, image_url, page_id, platform):
    """يبعت صورة للعميل عبر Meta attachment API — بيرجع True لو نجح"""
    page = bundle["pages"].get(page_id)
    if not page or not page.access_token or not image_url:
        return False
    try:
        r = requests.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": page.access_token},
            json={
                "recipient": {"id": sender_id},
                "message": {
                    "attachment": {
                        "type": "image",
                        "payload": {"url": image_url.strip(), "is_reusable": True},
                    }
                },
            },
            timeout=15,
        )
        if r.status_code == 200:
            return True
        print(f"⚠️ Send image failed ({r.status_code}): {r.text[:120]}")
        return False
    except Exception as e:
        print(f"⚠️ Send image error: {e}")
        return False


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
# VOICE TRANSCRIPTION — تحويل الرسائل الصوتية لنص (Whisper API)
# =====================================================================
def transcribe_voice(audio_url, access_token=None):
    """
    يحمّل رسالة صوتية من Meta ويحوّلها لنص عربي عبر OpenAI Whisper.
    بيرجع النص أو None لو التحويل مش متاح/فشل.

    متغير البيئة المطلوب: OPENAI_API_KEY (لو مش موجود، بيرجع None
    والبوت بيرد بالرسالة التوجيهية القديمة — degradation آمن)
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key or not audio_url:
        return None

    # 1) تحميل الملف الصوتي من Meta CDN
    audio_bytes = None
    for params in ({}, {"access_token": access_token} if access_token else {}):
        try:
            r = requests.get(audio_url, params=params, timeout=20)
            if r.status_code == 200 and r.content:
                audio_bytes = r.content
                break
        except Exception:
            continue
    if not audio_bytes:
        print("⚠️ Voice download failed")
        return None
    if len(audio_bytes) > 24 * 1024 * 1024:   # حد Whisper 25MB
        print("⚠️ Voice file too large")
        return None

    # 2) التحويل عبر Whisper
    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {openai_key}"},
            files={"file": ("voice.mp4", audio_bytes, "audio/mp4")},
            data={"model": "whisper-1", "language": "ar"},
            timeout=45,
        )
        if resp.status_code == 200:
            text = (resp.json().get("text") or "").strip()
            if text:
                print(f"🎤 Voice transcribed: {text[:60]}...")
                return text
        else:
            print(f"⚠️ Whisper error {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"⚠️ Whisper request failed: {e}")
    return None


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
            params={"fields": "id,page_label_name", "access_token": access_token},
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
            item_name = item.get("page_label_name") or item.get("name")
            if item_name == label.name:
                ids[page_id] = str(item["id"])
                label.meta_label_ids = json.dumps(ids, ensure_ascii=False)
                _db.session.commit()
                return str(item["id"])
    except Exception as e:
        print(f"⚠️ Labels lookup error: {e}")
        return None

    # 2) اعمل الـ label من جديد
    try:
        cr = requests.post(
            "https://graph.facebook.com/v18.0/me/custom_labels",
            params={"access_token": access_token},
            json={"page_label_name": label.name},
            timeout=10,
        )
        cj = cr.json()
        if "id" in cj:
            ids[page_id] = str(cj["id"])
            label.meta_label_ids = json.dumps(ids, ensure_ascii=False)
            _db.session.commit()
            print(f"   ➕ label جديدة على Meta: {label.name} ({cj['id']})")
            return str(cj["id"])
        elif "error" in cj:
            print(f"❌ فشل إنشاء label '{label.name}': {cj['error'].get('message')}")
    except Exception as e:
        print(f"⚠️ Label create error: {e}")
    return None


def _refetch_label_id(label, page_id, access_token):
    """يعيد جلب الـ label_id الصحيح من Meta ويحدّث الكاش (لو المخزّن قديم/غلط)"""
    from models import db as _db
    try:
        r = requests.get(
            "https://graph.facebook.com/v18.0/me/custom_labels",
            params={"fields": "id,page_label_name", "access_token": access_token},
            timeout=10,
        )
        for item in r.json().get("data", []):
            nm = item.get("page_label_name") or item.get("name")
            if nm == label.name:
                new_id = str(item["id"])
                ids = json.loads(label.meta_label_ids or "{}")
                ids[page_id] = new_id
                label.meta_label_ids = json.dumps(ids, ensure_ascii=False)
                _db.session.commit()
                return new_id
    except Exception as e:
        print(f"⚠️ refetch label id error: {e}")
    return None


def _do_label_post(label_id, sender_id, access_token):
    """ينفّذ POST تطبيق label — بيرجّع (status, resp)"""
    r = requests.post(
        f"https://graph.facebook.com/v18.0/{str(label_id)}/label",
        params={"access_token": access_token},
        headers={"Content-Type": "application/json"},
        json={"user": str(sender_id)},
        timeout=10,
    )
    return r.status_code, (r.json() if r.content else {})


def _apply_label_to_user(label, sender_id, page_id, access_token):
    label_id = _ensure_label_id(label, page_id, access_token)
    if not label_id:
        return
    try:
        status, resp = _do_label_post(label_id, sender_id, access_token)

        # ملاحظة: endpoint الـ label بيطبّق الليبل فعلياً حتى لو رجّع code 100.
        # ده سلوك معروف — الليبل بيتحط بنجاح. فبنعيد المحاولة فقط لو الـ ID اتغيّر.
        if status != 200 or not resp.get("success"):
            if resp.get("error", {}).get("code") == 100:
                fresh_id = _refetch_label_id(label, page_id, access_token)
                if fresh_id and str(fresh_id) != str(label_id):
                    print(f"🔄 label '{label.name}': ID اتحدّث {label_id}→{fresh_id}")
                    status, resp = _do_label_post(fresh_id, sender_id, access_token)

        if status == 200 and resp.get("success"):
            print(f"🏷️  '{label.name}' → {sender_id} ✅")
        elif status == 200:
            print(f"🏷️  '{label.name}' → {sender_id} ✅ (resp={resp})")
        else:
            # code 100 هنا غالباً بيكون الليبل اتطبّق بالفعل (سلوك API معروف)
            err = resp.get("error", {})
            if err.get("code") == 100:
                print(f"🏷️  '{label.name}' → {sender_id} (اتطبّق غالباً — API رجّع code 100)")
            else:
                print(f"⚠️ label '{label.name}': status={status} code={err.get('code')} — {err.get('message')}")
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
        # ── تنبيه تليجرام فوري: عميل طالب موظف ──
        _telegram_alert(bundle["tenant"],
            f"🙋 <b>عميل طالب يكلم موظف!</b>\n"
            f"آخر رسالة: «{user_message[:100]}»\n"
            f"البوت اتوقف عن الرد — افتح الإنبوكس ورد عليه.")
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

        # ── تنبيه تليجرام فوري: طلب جديد ──
        _telegram_alert(bundle["tenant"],
            f"🛒 <b>طلب جديد!</b>\n"
            f"المنتج: {new_order.get('product','')}\n"
            f"العميل: {new_order.get('name','')} — {new_order.get('phone','')}\n"
            f"العنوان: {new_order.get('address','')[:60]}")

        # ── Upsell وقت الطلب: اقترح منتج مكمّل بعد تأكيد الطلب ──
        if matched_product and (matched_product.cross_selling or "").strip() \
                and not state.get("upsell_sent"):
            cross_key = matched_product.cross_selling.split(",")[0].strip()
            cross_prod = next(
                (p for p in bundle["products"]
                 if p.product_key == cross_key and p.is_active), None)
            if cross_prod:
                time.sleep(1.0)
                upsell_msg = (
                    f"🎁 معلومة على الماشي: كتير من عملائنا بيضيفوا "
                    f"«{cross_prod.name}» مع طلبهم — {cross_prod.price_note or ''}. "
                    f"تحب أضيفهولك في نفس الطلب من غير شحن إضافي؟ 😊"
                )
                send_message(bundle, sender_id, upsell_msg, page_id, platform)
                state["upsell_sent"] = True
                print(f"🎁 Upsell offered: {cross_prod.product_key}")

    # ── صورة المنتج: تتبعت مرة واحدة لكل منتج (تقلل خطأ إرسال منتج غلط) ──
    if matched_product and (matched_product.image_urls or "").strip():
        if matched_product.product_key not in state.get("images_sent", []):
            first_img = matched_product.image_urls.split(",")[0].strip()
            if first_img:
                time.sleep(0.4)
                if send_image(bundle, sender_id, first_img, page_id, platform):
                    state.setdefault("images_sent", []).append(matched_product.product_key)
                    print(f"🖼️ Product image sent: {matched_product.product_key}")

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
        # ── تنبيه تليجرام فوري: عميل غاضب محتاج تدخل ──
        _telegram_alert(bundle["tenant"],
            f"🚨 <b>عميل غاضب محتاج تدخل!</b>\n"
            f"آخر رسالة: «{user_message[:100]}»\n"
            f"افتح الإنبوكس وتدخّل بسرعة قبل ما تخسره.")

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

    # الـ Timer thread محتاج app context عشان الـ DB queries تشتغل
    app_obj = batch.get("app_obj")
    if app_obj:
        with app_obj.app_context():
            do_process_message(
                tenant_id, sender_id, combined,
                batch["page_id"], batch["platform"],
                batch.get("image_b64"), batch.get("image_media_type", "image/jpeg"),
            )
    else:
        do_process_message(
            tenant_id, sender_id, combined,
            batch["page_id"], batch["platform"],
            batch.get("image_b64"), batch.get("image_media_type", "image/jpeg"),
        )


def buffer_message(bundle, sender_id, message, page_id, platform,
                   image_b64=None, image_media_type="image/jpeg"):
    """يضيف رسالة لطابور الانتظار ويبدأ/يجدد مؤقت الـ debounce الخاص بالـ tenant"""
    from flask import current_app
    tenant_id = bundle["tenant"].id
    debounce_seconds = bundle["bot_config"].debounce_seconds or 45
    key = (tenant_id, sender_id)
    try:
        app_obj = current_app._get_current_object()
    except RuntimeError:
        app_obj = None

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
                "app_obj": app_obj,
            }
        pending_messages[key]["app_obj"] = app_obj

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
        # الموديريتور رد قبل ما البوت يتفاعل مع العميل (أو حالة جديدة) —
        # ننشئ سجل كامل عشان الإيقاف يتسجّل ويتحفظ دايماً، مش يتجاهل
        page_id = str(event["sender"].get("id", ""))
        state = default_state(tenant_id, page_id, "facebook")
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
