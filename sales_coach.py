# -*- coding: utf-8 -*-
"""
sales_coach.py — مدرّب المبيعات 🎯

بيقرأ محادثات الفترة **بكل نتايجها** (اللي اشتروا واللي ماشتروش) ويطلع تقرير
عملي: إيه اللي بيفرق بين الاتنين، وإيه اللي محتاج يتصلّح.

🔑 مبدأ التصميم: طبقتين
  1. طبقة إحصائية على **كل** المحادثات (بدون AI = مجاناً) — الأرقام،
     الأسئلة المتكررة، ونقاط الموت. دي بتغطي 100% من الداتا مهما كان حجمها.
  2. طبقة قراءة عميقة بالـ AI على **عينة مختارة بذكاء** (مش أول N).
فحتى لو قرينا 60 محادثة من 150، الأرقام اللي بيتبني عليها التحليل من الـ 150 كلها.
"""

import time
from datetime import datetime, timedelta

EGYPT_UTC_OFFSET = 2

# ── مستويات العمق ────────────────────────────────────────────────────
DEPTH_LEVELS = {
    "light":    {"max_convos": 24,  "chars": 900, "corpus_cap": 24_000},
    "balanced": {"max_convos": 60,  "chars": 900, "corpus_cap": 58_000},
    "deep":     {"max_convos": 120, "chars": 800, "corpus_cap": 100_000},
}
DEFAULT_DEPTH = "balanced"

# توزيع العينة على الشرايح.
# ⚠️ الكاسبين ليهم أعلى نصيب: هم الأندر والأغلى معلوماتياً — من غيرهم
# مفيش مقارنة، والتقرير بيرجع "ليه خسرنا" بدل "إيه اللي بيكسب".
SEGMENT_SHARE = {
    "won":              0.30,
    "price_silent":     0.22,
    "engaged_no_order": 0.20,
    "objection":        0.16,
    "handoff":          0.06,
    "complaint":        0.06,
}

MIN_CONVOS_TO_RUN = 5


def check_and_run(app):
    """بيتنادى من الـ scheduler كل دورة — رخيص لو مفيش حاجة مستحقة"""
    from models import db, Tenant

    egypt_now = datetime.utcnow() + timedelta(hours=EGYPT_UTC_OFFSET)
    today_str = egypt_now.strftime("%Y-%m-%d")

    with app.app_context():
        tenants = Tenant.query.filter_by(is_active=True, coach_enabled=True).all()
        for tenant in tenants:
            if not tenant.telegram_chat_id or not tenant.telegram_enabled:
                continue
            if tenant.coach_last_sent == today_str:
                continue

            interval = tenant.coach_interval_days or 3
            if tenant.coach_last_sent:
                try:
                    last = datetime.strptime(tenant.coach_last_sent, "%Y-%m-%d")
                    if (egypt_now.date() - last.date()).days < interval:
                        continue
                except ValueError:
                    pass

            if egypt_now.hour < 11:
                continue

            tenant.coach_last_sent = today_str
            db.session.commit()

            try:
                run_for_tenant(app, tenant.id)
            except Exception as e:
                print(f"⚠️ Sales coach error for {tenant.slug}: {e}")


def run_for_tenant(app, tenant_id, send=True):
    """يبني ويبعت تقرير المدرّب لـ tenant واحد"""
    from models import db, Tenant
    import telegram_bot
    import ai_assist
    import json as _json

    with app.app_context():
        tenant = Tenant.query.get(tenant_id)
        if not tenant:
            return None

        interval = tenant.coach_interval_days or 3
        # 🔑 نافذة القراءة منفصلة عن دورية الإرسال.
        # ليه؟ لو التقرير يومي والنافذة يوم واحد، العينة بتبقى صغيرة جداً
        # والكاسبين ممكن يكونوا واحد أو اتنين — والمقارنة (اللي هي جوهر
        # التقرير) بتنهار. فبنسمح بتقرير يومي بنافذة 5 أيام مثلاً.
        lookback = getattr(tenant, "coach_lookback_days", 0) or interval

        depth = getattr(tenant, "coach_depth", None) or DEFAULT_DEPTH
        if depth not in DEPTH_LEVELS:
            depth = DEFAULT_DEPTH
        cfg = DEPTH_LEVELS[depth]

        segments, stats = _collect_segments(tenant, days=lookback, cfg=cfg)
        sampled = sum(len(v) for v in segments.values())

        if stats["total"] < MIN_CONVOS_TO_RUN:
            print(f"🎯 Sales coach: داتا قليلة لـ {tenant.slug} "
                  f"({stats['total']}) — اتأجل")
            return None

        bc = tenant.bot_config
        dialect = (getattr(bc, "dialect", None) or "مصري") if bc else "مصري"
        model_key = getattr(tenant, "coach_model", None) or "haiku"

        # توصيات التقرير اللي فات — عشان مايكررش نفسه ويتابع التنفيذ
        prev_fixes = []
        try:
            prev_fixes = _json.loads(getattr(tenant, "coach_last_fixes", None) or "[]")
        except (ValueError, TypeError):
            prev_fixes = []

        analysis = ai_assist.coach_analysis(
            segments=segments, stats=stats,
            business_name=tenant.business_name,
            business_description=(tenant.business_description or "")[:600],
            dialect=dialect,
            corpus_cap=cfg["corpus_cap"],
            model_key=model_key,
            previous_fixes=prev_fixes,
        )
        if not analysis:
            print(f"⚠️ Sales coach: التحليل فشل لـ {tenant.slug}")
            return None

        report = build_report(tenant, analysis, stats, lookback, sampled, depth)
        if send and tenant.telegram_chat_id:
            telegram_bot.send_message(tenant.telegram_chat_id, report)
            print(f"🎯 تقرير المدرّب اتبعت لـ {tenant.slug} — "
                  f"قرا {sampled} من {stats['total']} محادثة "
                  f"(نافذة {lookback}ي · {depth}/{model_key})")

        # احفظ عناوين التوصيات للتقرير الجاي
        try:
            titles = [f.get("title", "") for f in (analysis.get("fixes") or [])][:6]
            tenant.coach_last_fixes = _json.dumps(titles, ensure_ascii=False)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"⚠️ حفظ توصيات المدرّب فشل: {e}")

        return report


# =====================================================================
# الطبقة 1: إحصائيات على كل المحادثات (بدون AI)
# =====================================================================
def _collect_segments(tenant, days=3, cfg=None):
    """
    بيلف على **كل** محادثات الفترة: بيحسب الأرقام من كلها،
    وبيختار عينة ذكية للقراءة العميقة.
    """
    from bot_engine import list_tenant_states

    cfg = cfg or DEPTH_LEVELS[DEFAULT_DEPTH]
    cutoff = time.time() - (days * 86400)

    seg_keys = list(SEGMENT_SHARE.keys())
    pools = {k: [] for k in seg_keys}
    stats = {k: 0 for k in seg_keys}
    stats["total"] = 0

    questions, death_lines = [], []
    turns_won, turns_lost = [], []

    for st in list_tenant_states(tenant.id):
        if st.get("platform") == "demo":
            continue
        last = st.get("last_message") or st.get("created_at") or 0
        if last < cutoff:
            continue
        history = st.get("history", [])
        if len(history) < 2:
            continue

        stats["total"] += 1
        seg = _classify(st)
        stats[seg] += 1

        user_msgs = [(h.get("content") or "").strip()
                     for h in history if h.get("role") == "user"]

        for q in user_msgs:
            if 3 < len(q) < 120:
                questions.append(q)

        # 💀 نقطة الموت: آخر حاجة قالها العميل قبل ما يختفي
        if seg != "won" and user_msgs:
            tail = user_msgs[-1]
            if 2 < len(tail) < 120:
                death_lines.append(tail)

        (turns_won if seg == "won" else turns_lost).append(len(user_msgs))

        pools[seg].append({
            "text": _render_convo(history, cfg["chars"]),
            "turns": len(user_msgs),
            "ts": last,
        })

    segments = _pick_sample(pools, cfg["max_convos"])

    stats["top_questions"] = _cluster(questions, top=8, min_n=2)
    stats["death_points"] = _cluster(death_lines, top=6, min_n=2)
    stats["avg_turns_won"] = round(sum(turns_won) / len(turns_won), 1) if turns_won else 0
    stats["avg_turns_lost"] = round(sum(turns_lost) / len(turns_lost), 1) if turns_lost else 0
    stats["sampled"] = sum(len(v) for v in segments.values())
    return segments, stats


def _classify(st):
    """تصنيف المحادثة لشريحة (بالأولوية)"""
    if st.get("has_order"):
        return "won"
    if st.get("has_complaint"):
        return "complaint"
    if st.get("is_human_handoff"):
        return "handoff"
    if (st.get("price_quoted")
            and st.get("last_message", 0) <= st.get("price_quoted_time", 0)):
        return "price_silent"
    if st.get("stage") == "OBJECTION":
        return "objection"
    return "engaged_no_order"


def _pick_sample(pools, max_total):
    """
    اختيار ذكي بدل 'أول N':
      1. كل شريحة بتاخد حصتها من السقف (والكاسبين أكبر حصة)
      2. الحصة اللي مااتملتش بتترد للشرايح التانية (مفيش هدر)
      3. جوّه الشريحة: بنفضّل المحادثات **الأغنى** (رسائل أكتر = إشارة أوضح)
         وبعدين نرتبهم زمنياً عشان التحليل يشوف تطور الفترة
    """
    chosen = {}
    leftover = 0

    quotas = {}
    for seg, share in SEGMENT_SHARE.items():
        q = max(3, int(max_total * share))
        available = len(pools.get(seg, []))
        quotas[seg] = min(q, available)
        leftover += max(0, q - available)

    for seg in sorted(SEGMENT_SHARE, key=lambda s: -SEGMENT_SHARE[s]):
        if leftover <= 0:
            break
        room = len(pools.get(seg, [])) - quotas[seg]
        if room > 0:
            take = min(room, leftover)
            quotas[seg] += take
            leftover -= take

    for seg, items in pools.items():
        n = quotas.get(seg, 0)
        if n <= 0 or not items:
            chosen[seg] = []
            continue
        ranked = sorted(items, key=lambda x: (-x["turns"], -x["ts"]))[:n]
        ranked.sort(key=lambda x: x["ts"])
        chosen[seg] = [x["text"] for x in ranked]

    return chosen


def _render_convo(history, max_chars):
    """يحوّل المحادثة لنص مختصر للتحليل"""
    lines = []
    for h in history[-16:]:
        who = "عميل" if h.get("role") == "user" else "البوت"
        content = (h.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"{who}: {content[:170]}")
    return "\n".join(lines)[-max_chars:]


def _cluster(texts, top=8, min_n=2):
    """تجميع تقريبي للنصوص المتشابهة (أول 3 كلمات)"""
    from collections import Counter
    norm = []
    for t in texts:
        k = " ".join(t.split()[:3]).strip("؟?.,!، ")
        if len(k) > 2:
            norm.append(k)
    return [{"q": q, "n": n} for q, n in Counter(norm).most_common(top) if n >= min_n]


# =====================================================================
# بناء التقرير لتليجرام
# =====================================================================
def _esc(s):
    return (str(s or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


DEPTH_LABELS = {"light": "خفيف", "balanced": "متوازن", "deep": "عميق"}


def build_report(tenant, analysis, stats, interval, sampled=0, depth="balanced"):
    S = []
    total = stats.get("total", 0)
    read_note = (f" · قرا {sampled} منهم بالتفصيل"
                 if sampled and sampled < total else "")
    S.append(f"🎯 <b>تقرير المدرّب — {_esc(tenant.business_name)}</b>\n"
             f"<i>آخر {interval} أيام · {total} محادثة{read_note}</i>\n"
             f"━━━━━━━━━━━━━━━━━")

    won = stats.get("won", 0)
    cr = won / (total or 1) * 100
    S.append(
        f"📊 <b>المشهد</b>\n"
        f"🏆 اشتروا: <b>{won}</b> ({cr:.0f}%)\n"
        f"💸 سمعوا السعر وسكتوا: <b>{stats.get('price_silent', 0)}</b>\n"
        f"⚠️ اعترضوا: <b>{stats.get('objection', 0)}</b>\n"
        f"🤔 اتفاعلوا وماكملوش: <b>{stats.get('engaged_no_order', 0)}</b>\n"
        f"🙋 طلبوا موظف: <b>{stats.get('handoff', 0)}</b>   |   "
        f"🚨 شكاوى: <b>{stats.get('complaint', 0)}</b>"
    )

    aw, al = stats.get("avg_turns_won", 0), stats.get("avg_turns_lost", 0)
    if aw and al:
        S.append(f"💬 <b>متوسط رسائل العميل</b>\n"
                 f"في المحادثة اللي كسبت: <b>{aw}</b>   |   "
                 f"اللي ضاعت: <b>{al}</b>")

    if analysis.get("winning_pattern"):
        S.append(f"🏆 <b>إيه اللي بيقفل البيعة عندك؟</b>\n{_esc(analysis['winning_pattern'])}")

    if analysis.get("losing_pattern"):
        S.append(f"💔 <b>وإيه اللي بيضيّعها؟</b>\n{_esc(analysis['losing_pattern'])}")

    fixes = analysis.get("fixes") or []
    if fixes:
        lines = ["🔧 <b>اعمل إيه دلوقتي؟</b> <i>(بالأولوية)</i>"]
        icons = {"high": "🔴", "medium": "🟠", "low": "🟡"}
        for i, f in enumerate(fixes[:6], 1):
            ic = icons.get(str(f.get("priority", "")).lower(), "🔹")
            lines.append(f"\n{ic} <b>{i}. {_esc(f.get('title', ''))}</b>")
            if f.get("why"):
                lines.append(f"   <i>ليه: {_esc(f['why'])}</i>")
            if f.get("how"):
                lines.append(f"   ← {_esc(f['how'])}")
        S.append("\n".join(lines))

    gaps = analysis.get("knowledge_gaps") or []
    if gaps:
        lines = ["❓ <b>أسئلة البوت مكانش عارف يردّ عليها</b>",
                 "<i>ضيف الإجابات دي في بيانات المنتجات أو القواعد الذكية</i>"]
        for g in gaps[:5]:
            lines.append(f"• {_esc(g)}")
        S.append("\n".join(lines))

    # 💀 نقاط الموت — من كل المحادثات مش من العينة
    dp = stats.get("death_points") or []
    if dp:
        lines = ["💀 <b>آخر حاجة قالها العميل قبل ما يختفي</b>",
                 "<i>محسوبة من كل المحادثات — دي أهم نقطة تشتغل عليها</i>"]
        for d in dp[:5]:
            lines.append(f"• «{_esc(d['q'])}» — <b>{d['n']}</b> مرة")
        S.append("\n".join(lines))

    tq = stats.get("top_questions") or []
    if tq:
        lines = ["🔁 <b>أكتر أسئلة بتتكرر</b>"]
        for q in tq[:6]:
            lines.append(f"• «{_esc(q['q'])}» — <b>{q['n']}</b> مرة")
        S.append("\n".join(lines))

    if analysis.get("quick_win"):
        S.append(f"⚡ <b>أسرع مكسب</b>\n{_esc(analysis['quick_win'])}")

    S.append(f"━━━━━━━━━━━━━━━━━\n"
             f"🤖 تحليل تلقائي ({DEPTH_LABELS.get(depth, depth)}) — راجعه قبل ما تنفّذ")
    return "\n\n".join(S)
