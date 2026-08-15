# -*- coding: utf-8 -*-
"""
sales_coach.py — مدرّب المبيعات 🎯

بيقرأ محادثات الفترة **بكل نتايجها** (اللي اشتروا واللي ماشتروش) ويطلع تقرير
عملي: إيه اللي بيفرق بين الاتنين، وإيه اللي محتاج يتصلّح.

ليه مختلف عن التحليل الأسبوعي الموجود؟
التحليل الحالي (analyze_lost_conversations) بيبص على **الخسران بس** ويقول
"ليه ضاعوا". ده ناقص — لأن أهم معلومة في البيع هي **الفرق** بين المحادثة
اللي كسبت والمحادثة اللي ضاعت. المدرّب بياخد عينة من كل شريحة:
  🏆 اشتروا · 💸 سمعوا السعر وسكتوا · ⚠️ اعترضوا · 🤔 اتفاعلوا وماكملوش
  · 🙋 طلبوا موظف · 🚨 اشتكوا
ويقارنهم مع بعض.

التكلفة: تشغيلة واحدة كل 3 أيام بعيّنة محدودة (~24 محادثة مقصوصة) —
تقريباً 12-18 ألف توكن إدخال، يعني سنتات قليلة في الشهر.
"""

import json
import time
from datetime import datetime, timedelta

EGYPT_UTC_OFFSET = 2

# ── حدود العيّنة (تحكّم في التكلفة) ──────────────────────────────────
MAX_PER_SEGMENT   = 5      # أقصى محادثات من كل شريحة
MAX_TOTAL         = 24     # أقصى إجمالي
MAX_CHARS_PER_CONVO = 900  # قص كل محادثة
MIN_CONVOS_TO_RUN = 5      # ماتشتغلش على داتا قليلة (تقرير بلا قيمة)


def check_and_run(app):
    """
    بيتنادى من الـ scheduler كل دورة. بيشوف مين مستحق تقرير النهارده.
    رخيص جداً لو مفيش حاجة مستحقة (query واحدة).
    """
    from models import db, Tenant

    egypt_now = datetime.utcnow() + timedelta(hours=EGYPT_UTC_OFFSET)
    today_str = egypt_now.strftime("%Y-%m-%d")

    with app.app_context():
        tenants = Tenant.query.filter_by(is_active=True, coach_enabled=True).all()
        for tenant in tenants:
            if not tenant.telegram_chat_id or not tenant.telegram_enabled:
                continue
            if tenant.coach_last_sent == today_str:
                continue   # اتبعت النهارده بالفعل

            interval = tenant.coach_interval_days or 3
            if tenant.coach_last_sent:
                try:
                    last = datetime.strptime(tenant.coach_last_sent, "%Y-%m-%d")
                    if (egypt_now.date() - last.date()).days < interval:
                        continue
                except ValueError:
                    pass

            # ساعة الإرسال: 11 صباحاً بتوقيت مصر
            if egypt_now.hour < 11:
                continue

            # علّم قبل التشغيل عشان مانبعتش مرتين لو الدورة الجاية جت
            tenant.coach_last_sent = today_str
            db.session.commit()

            try:
                run_for_tenant(app, tenant.id)
            except Exception as e:
                print(f"⚠️ Sales coach error for {tenant.slug}: {e}")


def run_for_tenant(app, tenant_id, send=True):
    """يبني ويبعت تقرير المدرّب لـ tenant واحد. بيرجّع نص التقرير أو None."""
    from models import Tenant
    import telegram_bot
    import ai_assist

    with app.app_context():
        tenant = Tenant.query.get(tenant_id)
        if not tenant:
            return None

        interval = tenant.coach_interval_days or 3
        segments, stats = _collect_segments(tenant, days=interval)
        total = sum(len(v) for v in segments.values())

        if total < MIN_CONVOS_TO_RUN:
            print(f"🎯 Sales coach: داتا قليلة لـ {tenant.slug} ({total}) — اتأجل")
            return None

        bc = tenant.bot_config
        dialect = (getattr(bc, "dialect", None) or "مصري") if bc else "مصري"
        analysis = ai_assist.coach_analysis(
            segments=segments, stats=stats,
            business_name=tenant.business_name,
            business_description=(tenant.business_description or "")[:600],
            dialect=dialect,
        )
        if not analysis:
            print(f"⚠️ Sales coach: التحليل فشل لـ {tenant.slug}")
            return None

        report = build_report(tenant, analysis, stats, interval)
        if send and tenant.telegram_chat_id:
            telegram_bot.send_message(tenant.telegram_chat_id, report)
            print(f"🎯 تقرير المدرّب اتبعت لـ {tenant.slug} ({total} محادثة)")
        return report


# =====================================================================
# جمع العيّنات حسب النتيجة
# =====================================================================
def _collect_segments(tenant, days=3):
    """
    بيلف على محادثات الفترة ويقسّمها لشرايح حسب النتيجة.
    بيرجّع (segments, stats)
    """
    from bot_engine import list_tenant_states

    cutoff = time.time() - (days * 86400)
    segments = {
        "won": [], "price_silent": [], "objection": [],
        "engaged_no_order": [], "handoff": [], "complaint": [],
    }
    stats = {k: 0 for k in segments}
    stats["total"] = 0
    questions = []          # أسئلة العملاء (لرصد الأسئلة المتكررة)

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

        # تصنيف الشريحة (بالأولوية)
        if st.get("has_order"):
            seg = "won"
        elif st.get("has_complaint"):
            seg = "complaint"
        elif st.get("is_human_handoff"):
            seg = "handoff"
        elif (st.get("price_quoted")
              and (st.get("last_message", 0) <= st.get("price_quoted_time", 0))):
            seg = "price_silent"
        elif st.get("stage") == "OBJECTION":
            seg = "objection"
        else:
            seg = "engaged_no_order"

        stats[seg] += 1

        # نجمع أسئلة العميل للتحليل الكمي
        for h in history:
            if h.get("role") == "user":
                q = (h.get("content") or "").strip()
                if 3 < len(q) < 120:
                    questions.append(q)

        if len(segments[seg]) >= MAX_PER_SEGMENT:
            continue
        segments[seg].append(_render_convo(history))

    # نحافظ على السقف الإجمالي
    trimmed, count = {}, 0
    for seg, items in segments.items():
        keep = []
        for it in items:
            if count >= MAX_TOTAL:
                break
            keep.append(it)
            count += 1
        trimmed[seg] = keep

    stats["top_questions"] = _top_questions(questions)
    return trimmed, stats


def _render_convo(history):
    """يحوّل المحادثة لنص مختصر للتحليل"""
    lines = []
    for h in history[-14:]:
        who = "عميل" if h.get("role") == "user" else "البوت"
        content = (h.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"{who}: {content[:160]}")
    text = "\n".join(lines)
    return text[-MAX_CHARS_PER_CONVO:]


def _top_questions(questions, top=8):
    """أكتر أسئلة اتكررت (تجميع تقريبي بأول كلمتين + طول)"""
    from collections import Counter
    norm = []
    for q in questions:
        k = " ".join(q.split()[:3]).strip("؟?.,! ")
        if len(k) > 2:
            norm.append(k)
    return [{"q": q, "n": n} for q, n in Counter(norm).most_common(top) if n > 1]


# =====================================================================
# بناء التقرير لتليجرام
# =====================================================================
def _esc(s):
    return (str(s or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def build_report(tenant, analysis, stats, interval):
    S = []
    S.append(f"🎯 <b>تقرير المدرّب — {_esc(tenant.business_name)}</b>\n"
             f"<i>آخر {interval} أيام · {stats.get('total', 0)} محادثة</i>\n"
             f"━━━━━━━━━━━━━━━━━")

    # ── لقطة الأرقام ──
    won = stats.get("won", 0)
    total = stats.get("total", 0) or 1
    cr = won / total * 100
    S.append(
        f"📊 <b>المشهد</b>\n"
        f"🏆 اشتروا: <b>{won}</b> ({cr:.0f}%)\n"
        f"💸 سمعوا السعر وسكتوا: <b>{stats.get('price_silent', 0)}</b>\n"
        f"⚠️ اعترضوا: <b>{stats.get('objection', 0)}</b>\n"
        f"🤔 اتفاعلوا وماكملوش: <b>{stats.get('engaged_no_order', 0)}</b>\n"
        f"🙋 طلبوا موظف: <b>{stats.get('handoff', 0)}</b>   |   "
        f"🚨 شكاوى: <b>{stats.get('complaint', 0)}</b>"
    )

    # ── سر الكسب ──
    if analysis.get("winning_pattern"):
        S.append(f"🏆 <b>إيه اللي بيقفل البيعة عندك؟</b>\n{_esc(analysis['winning_pattern'])}")

    # ── سر الخسارة ──
    if analysis.get("losing_pattern"):
        S.append(f"💔 <b>وإيه اللي بيضيّعها؟</b>\n{_esc(analysis['losing_pattern'])}")

    # ── الإصلاحات بالأولوية ──
    fixes = analysis.get("fixes") or []
    if fixes:
        lines = ["🔧 <b>اعمل إيه دلوقتي؟</b> <i>(بالأولوية)</i>"]
        icons = {"high": "🔴", "medium": "🟠", "low": "🟡"}
        for i, f in enumerate(fixes[:5], 1):
            ic = icons.get(str(f.get("priority", "")).lower(), "🔹")
            lines.append(f"\n{ic} <b>{i}. {_esc(f.get('title', ''))}</b>")
            if f.get("why"):
                lines.append(f"   <i>ليه: {_esc(f['why'])}</i>")
            if f.get("how"):
                lines.append(f"   ← {_esc(f['how'])}")
        S.append("\n".join(lines))

    # ── أسئلة البوت مردش عليها كويس ──
    gaps = analysis.get("knowledge_gaps") or []
    if gaps:
        lines = ["❓ <b>أسئلة البوت مكانش عارف يردّ عليها</b>",
                 "<i>ضيف الإجابات دي في بيانات المنتجات أو القواعد الذكية</i>"]
        for g in gaps[:4]:
            lines.append(f"• {_esc(g)}")
        S.append("\n".join(lines))

    # ── أكتر أسئلة اتكررت (من الداتا مش من الـ AI) ──
    tq = stats.get("top_questions") or []
    if tq:
        lines = ["🔁 <b>أكتر أسئلة بتتكرر</b>"]
        for q in tq[:5]:
            lines.append(f"• «{_esc(q['q'])}» — <b>{q['n']}</b> مرة")
        S.append("\n".join(lines))

    # ── فرصة سريعة ──
    if analysis.get("quick_win"):
        S.append(f"⚡ <b>أسرع مكسب</b>\n{_esc(analysis['quick_win'])}")

    S.append("━━━━━━━━━━━━━━━━━\n🤖 تحليل تلقائي — راجعه قبل ما تنفّذ")
    return "\n\n".join(S)
