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
    """ينادي Claude ويطلب رد JSON فقط، مع parsing آمن ومرن"""
    response = client.messages.create(
        model=ASSIST_MODEL,
        max_tokens=max_tokens,
        system="ترد بصيغة JSON صحيحة فقط، بدون أي نص إضافي أو markdown fences.",
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    truncated = getattr(response, "stop_reason", None) == "max_tokens"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # محاولة إنقاذ: استخرج الـ JSON بين أول { وآخر } (بيتعامل مع نص زائد حوالين الرد)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    if truncated:
        print(f"⚠️ _ask_json: الرد اتقطع (max_tokens={max_tokens})")
        return {"error": "الرد كان طويل جداً — جرّب تختصر النص شوية وحاول تاني",
                "raw": raw[:400]}
    print(f"⚠️ _ask_json parse failed: {raw[:200]}")
    return {"error": "تعذّر تحليل رد الذكاء الاصطناعي", "raw": raw[:400]}


# =====================================================================
# 1) اقتراح شخصية البوت بناءً على وصف البزنس
# =====================================================================
def suggest_bot_persona(business_description, industry="", dialect="مصري"):
    """
    مدخل: "بنبيع منتجات عناية بالشعر طبيعية للستات"
    مخرج: persona + tone + اسم مقترح + أمثلة جمل افتتاحية
           + كلمات وافتتاحيات ممنوعة مخصصة للهجة المختارة
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
  "suggested_tone": "نبرة الصوت في 3-5 كلمات فقط (مثل: ودود وعملي / رسمي ومحترف / مرح وحماسي)",
  "example_greeting": "مثال على رسالة ترحيب بنفس اللهجة المطلوبة",
  "suggested_forbidden_words": ["كلمة1", "كلمة2"],
  "suggested_forbidden_openers": ["جملة بداية ضعيفة 1", "جملة بداية ضعيفة 2"]
}}

قواعد مهمة:
- "suggested_tone": لازم يكون قصير جداً (3-5 كلمات) — مش جملة طويلة.
- "suggested_forbidden_words": كلمات من **لهجات تانية** غير «{dialect}» عشان البوت مايخلطش.
  مثال لو اللهجة مصري: كلمات شامية/خليجية زي (هلق، شو، هيك، منيح، وش، أبغى، الحين، زين).
  مثال لو اللهجة سعودي أو إماراتي أو خليجي: كلمات مصرية زي (إيه، إزاي، دلوقتي، أوي، خالص، عايز).
  مثال لو اللهجة شامي: كلمات مصرية وخليجية.
  ضيف كمان أي كلمات رسمية/فصحى جامدة مش مناسبة للبيع بلهجة «{dialect}».
  من 6 لـ 12 كلمة.
- "suggested_forbidden_openers": جمل بداية ضعيفة أو مستهلكة تخلي الرد يبان آلي أو بارد،
  بلهجة «{dialect}» نفسها. أمثلة للفكرة: "أهلاً بحضرتك في خدمتك"، "كيف يمكنني مساعدتك؟"،
  "شكراً لتواصلك معنا". من 3 لـ 5 جمل مناسبة للهجة «{dialect}» والبزنس ده."""
    return _ask_json(prompt, max_tokens=2000)


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
"{short_input[:1500]}"

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
    result = _ask_json(prompt, max_tokens=3000)

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
    # حد أقصى للمدخل — الأوصاف الطويلة جداً بتستهلك tokens من غير فايدة
    desc = (raw_description or "").strip()[:2500]
    prompt = f"""مالك بزنس كتب الوصف ده عن شركته في خطوة إعداد البوت:
"{desc}"

قيّم هل الوصف كافي عشان نبني عليه بوت مبيعات ذكي، ولو ناقص اقترح أسئلة توضيحية.
رد بصيغة JSON بالضبط بهذا الشكل:
{{
  "is_sufficient": true أو false,
  "missing_points": ["نقطة ناقصة 1", "نقطة ناقصة 2"] أو [] لو كافي,
  "clarifying_questions": ["سؤال 1 يساعده يوضح أكتر", "سؤال 2"] أو [],
  "improved_example": "نسخة محسّنة ومقترحة من نفس الوصف، بنفس لغته، أوضح وأشمل"
}}
مهم: أقصى 4 نقاط ناقصة و3 أسئلة. الـ improved_example يكون منظم ومختصر (أقل من 1200 حرف)."""
    # tokens أعلى: الوصف المحسّن ممكن يكون طويل، والعربي بياخد tokens أكتر
    return _ask_json(prompt, max_tokens=4000)


# =====================================================================
# 6) استخراج بيانات المنتج من رابط الـ Landing Page
# =====================================================================
def _fetch_page_text(url, max_chars=4000):
    """يجلب نص الصفحة ويجرّدها من الـ HTML. بيرجّع str أو None عند الفشل."""
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
        print(f"⚠️ _fetch_page_text error ({url}): {e}")
        return None


def extract_business_from_url(store_url, dialect="مصري"):
    """
    يقرأ صفحة المتجر ويستخرج منها بيانات البزنس تلقائياً:
    اسم البزنس + المجال + وصف شامل (بيبيعوا إيه / لمين / إيه اللي يميزهم).
    بيرجّع dict فيه business_name / industry / business_description
    أو {"error": ...} لو فشل.
    """
    url = (store_url or "").strip()
    if not url:
        return {"error": "اكتب رابط المتجر أولاً"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    text = _fetch_page_text(url, max_chars=6000)
    if not text or len(text) < 80:
        return {"error": "مش قادر أقرأ الصفحة دي — تأكد إن الرابط شغّال وعام (مش محتاج تسجيل دخول)"}

    prompt = f"""ده محتوى صفحة متجر إلكتروني:
---
{text}
---

استخرج منه بيانات البزنس دي بدقة (بلهجة {dialect} طبيعية):

1. اسم البزنس (زي ما هو مكتوب في الموقع بالظبط)
2. المجال/الصناعة (كلمتين أو تلاتة — مثال: "مفروشات منزلية"، "منتجات طبية طبيعية")
3. وصف شامل منظّم في 3 أقسام بالظبط بالعناوين دي:
   "بيبيعوا إيه؟" — المنتجات والفئات بالتفصيل
   "لمين؟" — الفئة المستهدفة ومستوى الأسعار
   "إيه اللي يميزهم؟" — نقاط التميز، العروض، الخدمة

رد بصيغة JSON فقط:
{{
  "business_name": "الاسم",
  "industry": "المجال",
  "business_description": "بيبيعوا إيه؟\\n...\\n\\nلمين؟\\n...\\n\\nإيه اللي يميزهم؟\\n..."
}}

مهم: اعتمد على محتوى الصفحة بس، ماتخترعش معلومات. لو معلومة مش موجودة سيب مكانها فاضي.
الوصف يكون أقل من 1200 حرف."""

    result = _ask_json(prompt, max_tokens=4000)
    if result.get("error"):
        return result
    if not result.get("business_description"):
        return {"error": "الصفحة مافيهاش معلومات كافية عن البزنس — جرّب رابط الصفحة الرئيسية أو صفحة 'من نحن'"}
    return result


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

    result = _ask_json(prompt, max_tokens=3000)

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


def enrich_products_batch(products, business_description="", dialect="مصري"):
    """
    إثراء دفعة منتجات مستوردة بالمحتوى البيعي — بطلب AI واحد لكل دفعة صغيرة.
    بياخد: [{name, description, price_note}, ...]
    بيرجع: نفس الليستة مع إضافة: description (محسّن), keywords, features_text,
           who_benefits, results_timeline, closing_pitch, faq_text

    مصمّم للـ onboarding: منتجات مستوردة من متجر تبقى جاهزة للبيع فوراً.
    """
    if not products:
        return []

    # نبني وصف مختصر لكل منتج للـ prompt
    items_desc = []
    for i, p in enumerate(products):
        items_desc.append(
            f'{i}. الاسم: "{p.get("name","")}" | '
            f'الوصف الحالي: "{(p.get("description") or "")[:150] or "لا يوجد"}" | '
            f'السعر: {p.get("price_note") or "غير محدد"}'
        )

    prompt = f"""بزنس وصفه: "{business_description or "متجر إلكتروني"}"
دي منتجات اتستوردت من متجر التاجر ومحتاجة محتوى بيعي كامل عشان بوت المبيعات يبيعها:

{chr(10).join(items_desc)}

لكل منتج، ولّد محتوى بيعي احترافي بلهجة {dialect}. رد بصيغة JSON فقط:
{{
  "products": [
    {{
      "index": 0,
      "description": "وصف بيعي مقنع في 2-3 جمل يبرز الفايدة الأساسية",
      "keywords": "كلمات يسأل بيها العملاء فعلياً مفصولة بفاصلة (7-10 كلمات)",
      "features": ["ميزة 1", "ميزة 2", "ميزة 3"],
      "who_benefits": "وصف الجمهور المستهدف",
      "results_timeline": "متى تظهر النتيجة/الفايدة (لو منطقي للمنتج، وإلا اتركه فارغ)",
      "closing_pitch": "جملة إقناع لإغلاق البيع",
      "faq_pairs": [
        {{"q": "سؤال شائع", "a": "جواب مقنع"}},
        {{"q": "سؤال تاني", "a": "جواب"}}
      ]
    }}
  ]
}}
مهم: رجّع كل المنتجات بنفس الـ index بتاعها."""

    result = _ask_json(prompt, max_tokens=8000)
    if result.get("error"):
        # رد الـ AI اتقطع أو مش JSON صالح — نرمي exception عشان الـ caller يعيد المحاولة
        raise ValueError(f"AI enrichment parse failed: {result.get('error')}")
    enriched_map = {}
    for item in result.get("products", []):
        idx = item.get("index")
        if idx is None:
            continue
        if isinstance(item.get("features"), list):
            item["features_text"] = "\n".join(item["features"])
        if isinstance(item.get("faq_pairs"), list):
            item["faq_text"] = "\n".join(
                f"س: {q.get('q','')}\nج: {q.get('a','')}" for q in item["faq_pairs"]
            )
        enriched_map[idx] = item

    # ندمج الإثراء مع المنتجات الأصلية
    out = []
    for i, p in enumerate(products):
        merged = dict(p)
        enr = enriched_map.get(i, {})
        # الوصف: لو الأصلي فاضي أو قصير، استخدم المولّد
        if enr.get("description") and len(merged.get("description") or "") < 40:
            merged["description"] = enr["description"]
        # الكلمات: ندمج المولّدة مع الموجودة
        if enr.get("keywords"):
            merged["keywords"] = enr["keywords"]
        for field in ("features_text", "who_benefits", "results_timeline",
                      "closing_pitch", "faq_text"):
            if enr.get(field):
                merged[field] = enr[field]
        out.append(merged)
    return out


def analyze_lost_conversations(samples, dialect="مصري"):
    """
    تحليل نوعي بالـ AI لأسباب فقدان البيع — للتقرير الأسبوعي.
    samples: قائمة نصوص محادثات مختصرة (عملاء اهتموا ومااشتروش)
    بيرجع: {"breakdown": [{"reason","percent"}], "suggestions": [..]}
    """
    if not samples:
        return None

    convos = "\n\n---\n\n".join(
        f"محادثة {i+1}:\n{s[:800]}" for i, s in enumerate(samples[:12])
    )
    prompt = f"""أنت محلل مبيعات خبير. دي عينة من محادثات عملاء اهتموا بالمنتجات لكن ماكملوش الشراء:

{convos}

حلّل أسباب فقدان البيع وقسّمها بالنسب التقريبية، واقترح تحسينات عملية.
رد بصيغة JSON فقط:
{{
  "breakdown": [
    {{"reason": "السبب (مثلاً: اعتراض على السعر)", "percent": 40}},
    {{"reason": "سبب آخر", "percent": 30}}
  ],
  "suggestions": [
    "اقتراح عملي 1 بلهجة {dialect}",
    "اقتراح عملي 2"
  ]
}}
أقصى 4 أسباب و3 اقتراحات. خلّي الاقتراحات محددة وقابلة للتنفيذ."""

    result = _ask_json(prompt, max_tokens=1500)
    if result.get("error") or "breakdown" not in result:
        return None
    return result
