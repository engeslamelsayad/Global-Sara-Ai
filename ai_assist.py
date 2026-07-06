"""
ai_assist.py — اقتراحات مدعومة بالـ AI لمساعدة مالك البزنس في ملء الداشبورد

الفكرة: مالك البزنس مش لازم يكون خبير في كتابة system prompts أو تسويق.
يكتب وصف بسيط لبزنسه، والـ AI بيقترح:
  - شخصية بوت مناسبة
  - نصوص معالجة اعتراضات (غالي/مش متأكد/هفكر)
  - تحسين وصف منتج من سطر واحد بسيط
  - كلمات مفتاحية (RAG) لمنتج معين

كل اقتراح بيرجع كـ نص جاهز، مالك البزنس يقدر يعدله أو يقبله زي ما هو.
"""

import os
import json
import re
import anthropic
import requests as _requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

ASSIST_MODEL = "claude-haiku-4-5-20251001"


def _ask_json(prompt, max_tokens=800):
    """ينادي Claude ويطلب رد JSON فقط، مع parsing آمن"""
    response = client.messages.create(
        model=ASSIST_MODEL,
        max_tokens=max_tokens,
        system="ترد بصيغة JSON صحيحة فقط، بدون أي نص إضافي أو markdown fences.",
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "تعذّر تحليل رد الذكاء الاصطناعي", "raw": raw}


# =====================================================================
# 1) اقتراح شخصية البوت بناءً على وصف البزنس
# =====================================================================
def suggest_bot_persona(business_description, industry="", dialect="مصري"):
    """
    مدخل: "بنبيع منتجات عناية بالشعر طبيعية للستات"
    مخرج: persona + tone + اسم مقترح + أمثلة جمل افتتاحية
    """
    prompt = f"""بزنس صاحبه وصفه كده:
"{business_description}"
المجال: {industry or "غير محدد"}
اللهجة المطلوبة: {dialect}

اقترح شخصية بوت مبيعات مناسبة لهذا البزنس. رد بصيغة JSON بالضبط بهذا الشكل:
{{
  "suggested_bot_name": "اسم أنثوي بسيط مناسب للهجة",
  "suggested_age": رقم بين 22 و35,
  "suggested_persona": "وصف شخصية في جملة أو جملتين بنفس لغة الوصف المُدخل",
  "suggested_tone": "نبرة الصوت المناسبة (مثل: ودود وعملي / رسمي ومحترف / مرح وحماسي)",
  "example_greeting": "مثال على رسالة ترحيب بنفس اللهجة المطلوبة"
}}"""
    return _ask_json(prompt)


# =====================================================================
# 2) اقتراح نصوص معالجة الاعتراضات
# =====================================================================
def suggest_objection_responses(business_description, sample_product_name="", dialect="مصري"):
    """
    يقترح 3 ردود (غالي / مش متأكد / هفكر) مخصصة لطبيعة البزنس
    """
    prompt = f"""بزنس صاحبه وصفه كده:
"{business_description}"
مثال منتج من عندهم: "{sample_product_name or "غير محدد"}"
اللهجة: {dialect}

اقترح استراتيجية رد على 3 اعتراضات شائعة من العملاء، مناسبة لطبيعة هذا البزنس تحديداً
(مثلاً لو البزنس طبي قارن بزيارة دكتور، لو إلكترونيات قارن بمنتج منافس، إلخ).
رد بصيغة JSON بالضبط بهذا الشكل:
{{
  "objection_expensive": "استراتيجية الرد على 'غالي' في جملة أو جملتين",
  "objection_unsure": "استراتيجية الرد على 'مش متأكد/خايف' في جملة أو جملتين",
  "objection_later": "استراتيجية الرد على 'هفكر/بكره' في جملة أو جملتين"
}}"""
    return _ask_json(prompt)


# =====================================================================
# 3) تحسين وصف منتج + توليد كلمات مفتاحية (RAG)
# =====================================================================
def suggest_product_details(short_input, business_description="", dialect="مصري"):
    """
    مدخل: "كريم للحبوب فيه شي بتر وزيت شاي"
    مخرج: كل بيانات المنتج (نفس صيغة suggest_product_from_url) — اسم، وصف،
          كلمات مفتاحية، مفتاح المنتج، مميزات، من يستفيد، متى تظهر النتيجة،
          نص إغلاق، FAQ
    """
    prompt = f"""بزنس صاحبه وصفه: "{business_description or "غير محدد"}"
مالك البزنس كتب عن منتج كده (نص خام بسيط):
"{short_input}"

حوّل هذا لبيانات منتج احترافية كاملة. رد بصيغة JSON بالضبط بهذا الشكل:
{{
  "suggested_name": "اسم تسويقي جذاب للمنتج بلهجة {dialect}",
  "suggested_product_key": "مفتاح إنجليزي قصير بحروف صغيرة بدون مسافات (استخدم _ بين الكلمات، مثال: eczema_cream أو sleep_spray) — يعبّر عن المنتج",
  "suggested_description": "وصف بيعي مقنع في جملتين أو ثلاثة، يبرز الفايدة الأساسية",
  "suggested_keywords": "كلمة1,كلمة2,كلمة3,كلمة4 (كلمات يستخدمها العملاء فعلياً للسؤال عن المشكلة دي، مفصولة بفاصلة بدون مسافات)",
  "features": ["ميزة 1", "ميزة 2", "ميزة 3"],
  "who_benefits": "من يستفيد من هذا المنتج (وصف الجمهور المستهدف)",
  "results_timeline": "متى تظهر النتيجة بعد الاستخدام",
  "closing_pitch": "جملة إقناع قوية لإغلاق البيع لما العميل مش متأكد",
  "faq_pairs": [
    {{"q": "سؤال شائع 1", "a": "جواب مقنع 1"}},
    {{"q": "سؤال شائع 2", "a": "جواب مقنع 2"}}
  ]
}}"""
    result = _ask_json(prompt, max_tokens=1200)

    # حوّل faq_pairs لـ text بسيط للـ textarea
    if "faq_pairs" in result and isinstance(result["faq_pairs"], list):
        result["faq_text"] = "\n".join(
            f"س: {item.get('q','')}\nج: {item.get('a','')}"
            for item in result["faq_pairs"]
        )

    # حوّل features list لـ text بسيط
    if "features" in result and isinstance(result["features"], list):
        result["features_text"] = "\n".join(result["features"])

    return result


# =====================================================================
# 4) اقتراح كلمات مفتاحية إضافية لفئة معينة (شكاوى/طلب موظف)
# =====================================================================
def suggest_keywords_for_category(category, business_description="", existing_keywords=None):
    """
    category: "complaint" أو "human"
    يقترح كلمات إضافية مصرية شائعة ممكن العميل يستخدمها، غير الموجودة بالفعل
    """
    existing_keywords = existing_keywords or []
    category_label = {
        "complaint": "شكوى أو غضب من المنتج/الخدمة",
        "human": "طلب التحدث مع موظف بشري",
    }.get(category, category)

    prompt = f"""بزنس صاحبه وصفه: "{business_description or "غير محدد"}"
الفئة المطلوبة: كلمات تدل على "{category_label}"
الكلمات الموجودة بالفعل: {", ".join(existing_keywords) or "لا يوجد"}

اقترح 10 كلمات أو عبارات مصرية إضافية شائعة (غير مكررة مع الموجود) ممكن عميل يكتبها
في هذا السياق. رد بصيغة JSON بالضبط بهذا الشكل:
{{
  "suggested_keywords": ["كلمة1", "كلمة2", "..."]
}}"""
    return _ask_json(prompt)


# =====================================================================
# 5) مراجعة شاملة لوصف البزنس (Onboarding helper)
# =====================================================================
def review_business_description(raw_description):
    """
    يساعد مالك البزنس وهو بيكتب الوصف لأول مرة — يديله أمثلة وأسئلة توضيحية
    لو الوصف ناقص أو مختصر جداً
    """
    prompt = f"""مالك بزنس كتب الوصف ده عن شركته في خطوة إعداد البوت:
"{raw_description}"

قيّم هل الوصف كافي عشان نبني عليه بوت مبيعات ذكي، ولو ناقص اقترح أسئلة توضيحية.
رد بصيغة JSON بالضبط بهذا الشكل:
{{
  "is_sufficient": true أو false,
  "missing_points": ["نقطة ناقصة 1", "نقطة ناقصة 2"] أو [] لو كافي,
  "clarifying_questions": ["سؤال 1 يساعده يوضح أكتر", "سؤال 2"] أو [],
  "improved_example": "نسخة محسّنة ومقترحة من نفس الوصف، بنفس لغته، أوضح وأشمل"
}}"""
    return _ask_json(prompt)


# =====================================================================
# 6) استخراج بيانات المنتج من رابط الـ Landing Page
# =====================================================================
def _fetch_page_text(url, max_chars=4000):
    """يجلب نص الصفحة ويجرّدها من الـ HTML"""
    try:
        resp = _requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ProductBot/1.0)"
        })
        resp.raise_for_status()
        # إزالة HTML tags بطريقة بسيطة
        text = re.sub(r"<style[^>]*>.*?</style>", " ", resp.text, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return None, str(e)


def suggest_product_from_url(url, business_description="", dialect="مصري"):
    """
    يجلب محتوى الـ landing page ويستخرج منها كل بيانات المنتج المطلوبة.

    مدخل: رابط صفحة المنتج (eecm.shop أو أي موقع)
    مخرج: نفس صيغة suggest_product_details + حقول إضافية (features, faq, closing_pitch)
    """
    page_text = _fetch_page_text(url)
    if isinstance(page_text, tuple):
        # خطأ في الجلب
        return {"error": f"تعذّر جلب الصفحة: {page_text[1]}"}
    if not page_text or len(page_text) < 50:
        return {"error": "الصفحة فارغة أو لم يتم جلبها — تأكد من الرابط"}

    prompt = f"""أنت محلل منتجات محترف. فيما يلي محتوى نصي من صفحة منتج:

---
{page_text}
---

بيانات البزنس (إن وُجدت): "{business_description or 'غير محدد'}"
اللهجة المطلوبة في الإخراج: {dialect}

استخرج بيانات المنتج من هذا النص بدقة. رد بصيغة JSON فقط:
{{
  "suggested_name": "اسم تسويقي جذاب للمنتج",
  "suggested_product_key": "مفتاح إنجليزي قصير بحروف صغيرة بدون مسافات (استخدم _ بين الكلمات، مثال: eczema_cream) — يعبّر عن المنتج",
  "suggested_description": "وصف بيعي مقنع في 2-3 جمل يبرز الفايدة الأساسية",
  "suggested_keywords": "كلمة1,كلمة2,كلمة3,كلمة4,كلمة5 (كلمات يستخدمها العملاء للسؤال عن المشكلة)",
  "features": ["ميزة 1", "ميزة 2", "ميزة 3"],
  "who_benefits": "من يستفيد من هذا المنتج (وصف الجمهور المستهدف)",
  "results_timeline": "متى تظهر النتيجة بعد الاستخدام",
  "closing_pitch": "جملة إقناع قوية لإغلاق البيع لما العميل مش متأكد",
  "faq_pairs": [
    {{"q": "سؤال شائع 1", "a": "جواب مقنع 1"}},
    {{"q": "سؤال شائع 2", "a": "جواب مقنع 2"}}
  ],
  "price_note": "معلومة السعر لو ذُكرت في الصفحة، وإلا اتركها فارغة"
}}"""

    result = _ask_json(prompt, max_tokens=1200)

    # حوّل faq_pairs لـ text بسيط للـ textarea
    if "faq_pairs" in result and isinstance(result["faq_pairs"], list):
        faq_text = "\n".join(
            f"س: {item.get('q','')}\nج: {item.get('a','')}"
            for item in result["faq_pairs"]
        )
        result["faq_text"] = faq_text

    # حوّل features list لـ text بسيط
    if "features" in result and isinstance(result["features"], list):
        result["features_text"] = "\n".join(result["features"])

    return result
