"""
model_catalog.py — النماذج المتاحة للرد على العملاء + تقدير التكلفة

التقدير مبني على **قياس فعلي** من محرك البوت (مش تخمين):
  - الـ system prompt: ~7,340 حرف ≈ 2,940 token — منهم 93% مكاشّة (cache)
  - الرد: حد أقصى 600 token (متوسط فعلي ~200)
  - الذاكرة: آخر 16 رسالة

الأسعار من صفحة Anthropic الرسمية (أكتوبر 2026) — لكل مليون token:
  Haiku 5.5   : $0.10 إدخال / $0.50 إخراج  (للـ prompts لحد 100K token)
  Sonnet 5.5  : $2 / $10
  Opus 5.5    : $4 / $20
  cache read  = 10% من سعر الإدخال · cache write = 1.25x

📌 تحديث أكتوبر 2026: اتنقلنا لجيل 5.5 كله (Haiku 4.5 → 5.5، Sonnet 5 → 5.5،
   Opus 5 → 5.5). Haiku 5.5 أرخص 10 مرات من 4.5، و Opus 5.5 أرخص من Opus 5.
   ⚠️ الجيل ده بيفكّر (thinking) افتراضياً، والتفكير بيتحسب من max_tokens
   وبيبطّأ الرد — عشان كده request_kwargs() تحت بتقفله (أو بتقلله) لردود
   العملاء. وكمان الـ tokenizer الجديد بيعدّ نفس النص ~30% tokens أكتر.

⚠️ الأسعار ممكن تتغير — راجع claude.com/pricing قبل أي قرار تسعير مهم.
"""

# ═══ ثوابت الاستهلاك (مقيسة من الكود) ═══
# (اتقاسوا على Haiku 4.5 — مضروبين في 1.3 عشان الـ tokenizer الجديد بتاع جيل 5.5)
TOKENIZER_FACTOR = 1.3
CACHED_TOKENS = int(2740 * TOKENIZER_FACTOR)   # الجزء الثابت من الـ prompt (مكاشّ)
FRESH_TOKENS = int(875 * TOKENIZER_FACTOR)     # الجزء المتغير + الذاكرة + رسالة العميل
OUTPUT_TOKENS = int(200 * TOKENIZER_FACTOR)    # متوسط الرد الفعلي (الحد 600)
MSGS_PER_CONVO = 8        # متوسط ردود البوت في المحادثة الواحدة

DEFAULT_MODEL_ID = "claude-haiku-5-5"

MODELS = [
    {
        "id": "claude-haiku-5-5",
        "label": "Haiku 5.5",
        "tagline": "سريع واقتصادي جداً — الأنسب لمعظم الحالات",
        "in_price": 0.10, "out_price": 0.50,
        "badge": "🟢 موصى به",
        "badge_color": "#10b981",
        "notes": "ردود سريعة جداً وتكلفة منخفضة جداً. ممتاز في البيع المباشرة واتباع التعليمات.",
    },
    {
        "id": "claude-sonnet-5-5",
        "label": "Sonnet 5.5",
        "tagline": "أذكى في الإقناع والمحادثات المعقّدة",
        "in_price": 2.0, "out_price": 10.0,
        "badge": "⚡ أقوى",
        "badge_color": "#7c3aed",
        "notes": "أفضل في التعامل مع الاعتراضات الصعبة والأسئلة المركّبة — بتكلفة أعلى بكتير من Haiku.",
    },
    {
        "id": "claude-opus-5-5",
        "label": "Opus 5.5",
        "tagline": "الأقوى — للحالات الاستثنائية",
        "in_price": 4.0, "out_price": 20.0,
        "badge": "💎 مكلف",
        "badge_color": "#ef4444",
        "notes": "غالباً مبالغة لبوت مبيعات، وأبطأ شوية (بيفكّر دايماً). استخدمه لو منتجاتك عالية القيمة جداً.",
    },
]

# النماذج القديمة → بديلها في الجيل الحالي (للـ migration ولأي قيمة قديمة متخزنة)
LEGACY_UPGRADES = {
    "claude-haiku-4-5-20251001": "claude-haiku-5-5",
    "claude-haiku-4-5":          "claude-haiku-5-5",
    "claude-sonnet-4-6":         "claude-sonnet-5-5",
    "claude-sonnet-5":           "claude-sonnet-5-5",
    "claude-opus-4-8":           "claude-opus-5-5",
    "claude-opus-5":             "claude-opus-5-5",
}

# هامش tokens إضافي للتفكير — عشان التفكير مايكلش الرد نفسه ويطلع فاضي
THINK_HEADROOM = 4000


def request_kwargs(model_id, max_tokens, think=False):
    """
    باقي parameters الـ messages.create حسب الموديل.

    جيل 5.5 بيفكّر افتراضياً، والتفكير بيتحسب من max_tokens — فرد عميل
    بحد 600 token ممكن يخلص في التفكير ويطلع فاضي. فـ:
      - think=False (ردود العملاء + المهام السريعة): التفكير مقفول
        (Haiku: disabled · Sonnet: between_tools — الـ disabled بيرجّع 400 عليه)
        و Opus 5.5 مابيتقفلش أصلاً → effort low + هامش tokens.
      - think=True (تحليلات عميقة زي مدرّب المبيعات): adaptive + هامش tokens.
    """
    m = (model_id or "").strip()
    if m.startswith("claude-haiku-5-5"):
        if think:
            return {"max_tokens": max_tokens + THINK_HEADROOM,
                    "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}
        return {"max_tokens": max_tokens,
                "thinking": {"type": "disabled"}, "output_config": {"effort": "medium"}}
    if m.startswith("claude-sonnet-5-5"):
        if think:
            return {"max_tokens": max_tokens + THINK_HEADROOM,
                    "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}
        return {"max_tokens": max_tokens,
                "thinking": {"type": "between_tools"}, "output_config": {"effort": "medium"}}
    if m.startswith("claude-opus-5-5"):
        return {"max_tokens": max_tokens + THINK_HEADROOM,
                "output_config": {"effort": "medium" if think else "low"}}
    return {"max_tokens": max_tokens}


def response_text(resp):
    """
    نص الرد من الـ content blocks حسب النوع — مش content[0]، لأن الرد ممكن
    يبدأ بـ thinking block. بيرجّع "" لو مفيش نص (رفض أو اتقطع في التفكير).
    """
    return "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", "") == "text").strip()


def estimate_monthly_cost(model, convos=10000):
    """
    تقدير تكلفة الشهر بالدولار لعدد محادثات معيّن.
    بيرجّع dict فيه التفصيل.
    """
    in_p = model["in_price"] / 1_000_000
    out_p = model["out_price"] / 1_000_000
    cache_read_p = in_p * 0.10     # قراءة الكاش = 10% من الإدخال
    cache_write_p = in_p * 1.25    # كتابة الكاش = 1.25x

    msgs = convos * MSGS_PER_CONVO
    # كتابة الكاش: مرة لكل محادثة تقريباً (TTL 5 دقايق)
    cache_write = convos * CACHED_TOKENS * cache_write_p
    cache_read = msgs * CACHED_TOKENS * cache_read_p
    fresh = msgs * FRESH_TOKENS * in_p
    output = msgs * OUTPUT_TOKENS * out_p
    total = cache_write + cache_read + fresh + output

    return {
        "total": round(total, 2),
        "per_convo": round(total / convos, 4) if convos else 0,
        "breakdown": {
            "cache_write": round(cache_write, 2),
            "cache_read": round(cache_read, 2),
            "fresh_input": round(fresh, 2),
            "output": round(output, 2),
        },
    }


def models_with_costs(convos=10000):
    """قائمة النماذج مع تكلفة كل واحد — للعرض في الداشبورد"""
    out = []
    for m in MODELS:
        est = estimate_monthly_cost(m, convos)
        out.append({**m, "cost": est["total"], "per_convo": est["per_convo"]})
    return out


def get_model(model_id):
    """بيرجّع بيانات نموذج بالـ id (بعد ترقية القديم)، أو الافتراضي (Haiku)"""
    model_id = LEGACY_UPGRADES.get(model_id, model_id)
    return next((m for m in MODELS if m["id"] == model_id), MODELS[0])
