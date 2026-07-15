"""
model_catalog.py — النماذج المتاحة للرد على العملاء + تقدير التكلفة

التقدير مبني على **قياس فعلي** من محرك البوت (مش تخمين):
  - الـ system prompt: ~7,340 حرف ≈ 2,940 token — منهم 93% مكاشّة (cache)
  - الرد: حد أقصى 600 token (متوسط فعلي ~200)
  - الذاكرة: آخر 16 رسالة

الأسعار من صفحة Anthropic الرسمية (يوليو 2026) — لكل مليون token:
  Haiku 4.5   : $1 إدخال / $5 إخراج
  Sonnet 4.6  : $3 / $15
  Opus 4.8    : $5 / $25
  cache read  = 10% من سعر الإدخال · cache write = 1.25x

⚠️ الأسعار ممكن تتغير — راجع claude.com/pricing قبل أي قرار تسعير مهم.
"""

# ═══ ثوابت الاستهلاك (مقيسة من الكود) ═══
CACHED_TOKENS = 2740      # الجزء الثابت من الـ prompt (مكاشّ)
FRESH_TOKENS = 875        # الجزء المتغير + الذاكرة + رسالة العميل
OUTPUT_TOKENS = 200       # متوسط الرد الفعلي (الحد 600)
MSGS_PER_CONVO = 8        # متوسط ردود البوت في المحادثة الواحدة

MODELS = [
    {
        "id": "claude-haiku-4-5-20251001",
        "label": "Haiku 4.5",
        "tagline": "سريع واقتصادي — الأنسب لمعظم الحالات",
        "in_price": 1.0, "out_price": 5.0,
        "badge": "🟢 موصى به",
        "badge_color": "#10b981",
        "notes": "ردود سريعة جداً وتكلفة منخفضة. ذكاؤه قريب من Sonnet في مهام البيع المباشرة.",
    },
    {
        "id": "claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "tagline": "أذكى في الإقناع والمحادثات المعقّدة",
        "in_price": 3.0, "out_price": 15.0,
        "badge": "⚡ أقوى",
        "badge_color": "#7c3aed",
        "notes": "أفضل في التعامل مع الاعتراضات الصعبة والأسئلة المركّبة — بتكلفة أعلى ~3x.",
    },
    {
        "id": "claude-opus-4-8",
        "label": "Opus 4.8",
        "tagline": "الأقوى — للحالات الاستثنائية",
        "in_price": 5.0, "out_price": 25.0,
        "badge": "💎 مكلف",
        "badge_color": "#ef4444",
        "notes": "غالباً مبالغة لبوت مبيعات. استخدمه لو منتجاتك عالية القيمة جداً.",
    },
]


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
    """بيرجّع بيانات نموذج بالـ id، أو الافتراضي (Haiku)"""
    return next((m for m in MODELS if m["id"] == model_id), MODELS[0])
