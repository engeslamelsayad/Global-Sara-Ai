"""
telegram_bot.py — تكامل Telegram للتقارير الأسبوعية

الطريقة: bot مركزي واحد للمنصة كلها.
التاجر بيربط حسابه عن طريق:
1. ياخد كود ربط من الداشبورد (مثلاً: LINK-A3F9)
2. يفتح bot المنصة على تليجرام ويبعت الكود
3. الـ bot بيربط الـ chat_id بتاعه بالـ tenant

المتغيرات المطلوبة في البيئة:
  TELEGRAM_BOT_TOKEN — توكن الـ bot المركزي (من @BotFather)
"""

import os
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def is_configured():
    """هل الـ bot المركزي متظبط؟"""
    return bool(TELEGRAM_BOT_TOKEN)


TG_MAX_CHARS = 4000   # حد تليجرام 4096 — بنسيب هامش أمان


def send_message(chat_id, text, parse_mode="HTML"):
    """
    يبعت رسالة لمحادثة تليجرام.
    لو النص أطول من حد تليجرام، بيتقسم تلقائياً على رسائل متتالية
    (بيقسم عند فواصل الأقسام عشان مايكسرش تنسيق الـ HTML).
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
    parts = _split_message(text) if len(text) > TG_MAX_CHARS else [text]
    ok_all = True
    for p in parts:
        ok_all &= _send_single(chat_id, p, parse_mode)
    return ok_all


def _split_message(text):
    """يقسم النص الطويل على أجزاء — بيفضّل القطع عند سطر فاضي (نهاية قسم)"""
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) <= TG_MAX_CHARS:
            current = candidate
        else:
            if current:
                parts.append(current)
            # لو البلوك نفسه أطول من الحد، نقصّه
            while len(block) > TG_MAX_CHARS:
                parts.append(block[:TG_MAX_CHARS])
                block = block[TG_MAX_CHARS:]
            current = block
    if current:
        parts.append(current)
    return parts


def _send_single(chat_id, text, parse_mode="HTML"):
    try:
        r = requests.post(
            f"{API_BASE}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"⚠️ Telegram {r.status_code}: {r.text[:120]}")
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")
        return False


def get_updates(offset=None):
    """يجيب الرسائل الجديدة للـ bot (long polling)"""
    if not TELEGRAM_BOT_TOKEN:
        return []
    try:
        params = {"timeout": 20}
        if offset:
            params["offset"] = offset
        r = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=30)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        print(f"⚠️ Telegram getUpdates error: {e}")
    return []


def process_link_messages(app):
    """
    يفحص الرسائل الجديدة اللي جاية للـ bot ويربط الأكواد بالـ tenants.
    بيشتغل في الـ scheduler thread.

    لما تاجر يبعت كود الربط (زي LINK-A3F9)، بنلاقي الـ tenant اللي عنده
    الكود ده ونحفظ الـ chat_id بتاعه.
    """
    from models import db, Tenant

    # نستخدم ملف بسيط لتخزين آخر offset اتعالج (عشان منكررش)
    offset_file = "/tmp/tg_offset.txt"
    last_offset = None
    try:
        with open(offset_file) as f:
            last_offset = int(f.read().strip())
    except Exception:
        pass

    updates = get_updates(offset=(last_offset + 1) if last_offset else None)
    if not updates:
        return

    max_offset = last_offset or 0
    for upd in updates:
        max_offset = max(max_offset, upd.get("update_id", 0))
        msg = upd.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()

        if not chat_id or not text:
            continue

        # أمر /start
        if text.startswith("/start"):
            send_message(chat_id,
                "👋 أهلاً بك في بوت التقارير!\n\n"
                "عشان تربط متجرك، ابعت كود الربط اللي ظاهر في لوحة التحكم "
                "(بيبدأ بـ LINK-)")
            continue

        # كود ربط؟
        if text.upper().startswith("LINK-"):
            code = text.upper().strip()
            with app.app_context():
                tenant = Tenant.query.filter_by(telegram_link_code=code).first()
                if tenant:
                    tenant.telegram_chat_id = chat_id
                    tenant.telegram_enabled = True
                    tenant.telegram_link_code = None  # الكود يُستهلك مرة واحدة
                    db.session.commit()
                    send_message(chat_id,
                        f"✅ تم ربط متجر <b>{tenant.business_name}</b> بنجاح!\n\n"
                        f"هتوصلك تقارير أسبوعية كل يوم سبت الساعة 9 صباحاً 📊")
                    print(f"🔗 Telegram linked for tenant {tenant.slug} → chat {chat_id}")
                else:
                    send_message(chat_id,
                        "❌ الكود ده مش صحيح أو انتهت صلاحيته.\n"
                        "روح للوحة التحكم وخد كود جديد.")

    # احفظ آخر offset
    try:
        with open(offset_file, "w") as f:
            f.write(str(max_offset))
    except Exception:
        pass


def _esc(s):
    """تهريب رموز HTML عشان أسماء المنتجات ماتكسرش تنسيق تليجرام"""
    return (str(s or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def build_weekly_report(tenant, data, loss_analysis=None):
    """
    يبني التقرير الأسبوعي — نسخة غنية بتعكس الداشبورد:
    نظرة سريعة + الطلبات + رؤى المنتجات + نقاط الصمت + سلّم المتابعات
    + الفرص الضايعة + أداء الإعلانات + تنبيهات + تحليل AI
    """
    lost = data.get("lost_opportunities", {})
    sil  = data.get("silence", {})
    fu   = data.get("fu_by_stage", {}) or {}
    S = []   # الأقسام

    # ══ 1) نظرة سريعة ══
    conv_rate = data.get("conversion_rate", 0)
    rate_icon = "🟢" if conv_rate >= 20 else ("🟠" if conv_rate >= 8 else "🔴")
    S.append(
        f"📊 <b>التقرير الأسبوعي — {_esc(tenant.business_name)}</b>\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    S.append(
        f"🗓 <b>نظرة سريعة</b>\n"
        f"💬 محادثات: <b>{data.get('total_conversations', 0)}</b>"
        f"   |   🟢 نشطين دلوقتي: <b>{data.get('active_last_hour', 0)}</b>\n"
        f"{rate_icon} معدل التحويل: <b>{conv_rate}%</b>"
    )

    # ══ 2) الطلبات ══
    orders_lines = [
        f"🛒 <b>الطلبات</b>",
        f"آخر 24 ساعة: <b>{data.get('orders_last_24h', 0)}</b>   |   "
        f"آخر 7 أيام: <b>{data.get('orders_last_7d', 0)}</b>",
        f"آخر 30 يوم: <b>{data.get('orders_last_30d', 0)}</b>   |   "
        f"الإجمالي: <b>{data.get('total_orders', 0)}</b>",
    ]
    top_products = sorted(data.get("orders_by_product", {}).items(),
                          key=lambda x: x[1], reverse=True)
    if top_products:
        orders_lines.append("\n🏆 <b>الأكتر مبيعاً:</b>")
        for name, cnt in top_products[:3]:
            orders_lines.append(f"• {_esc(name)}: <b>{cnt}</b> طلب")
    S.append("\n".join(orders_lines))

    # ══ 3) رؤى المنتجات ══
    pins = data.get("product_insights", [])
    if pins:
        lines = ["🔬 <b>رؤى المنتجات</b> <i>(سألوا → طلبوا → تحويل)</i>"]
        for r in pins[:5]:
            c = r.get("conversion", 0)
            icon = "🟢" if c >= 20 else ("🟠" if c >= 8 else "🔴")
            lines.append(
                f"{icon} <b>{_esc(r['name'])[:28]}</b>: "
                f"{r['asked']} → {r['orders']} → <b>{c:.0f}%</b>")
            # تفصيل الاعتراضات لو موجودة
            objs = []
            if r.get("expensive"):    objs.append(f"غالي {r['expensive']}")
            if r.get("unsure"):       objs.append(f"مش متأكد {r['unsure']}")
            if r.get("later"):        objs.append(f"بعدين {r['later']}")
            if r.get("price_silent"): objs.append(f"شاف السعر وسكت {r['price_silent']}")
            if objs:
                lines.append(f"    ↳ <i>{' · '.join(objs)}</i>")
        S.append("\n".join(lines))

    # ══ 4) فين العملاء بيسكتوا ══
    if any(sil.get(k) for k in ("after_price", "after_obj", "first_msg", "interested")):
        prr = sil.get("price_reply_rate", 0)
        prr_icon = "🟢" if prr >= 50 else ("🟠" if prr >= 25 else "🔴")
        S.append(
            f"🔇 <b>فين العملاء بيسكتوا؟</b>\n"
            f"💸 شافوا السعر وسكتوا: <b>{sil.get('after_price', 0)}</b>\n"
            f"⚠️ اعترضوا وسكتوا: <b>{sil.get('after_obj', 0)}</b>\n"
            f"👋 سألوا سؤال وسكتوا: <b>{sil.get('first_msg', 0)}</b>\n"
            f"❤️ كانوا مهتمين وسكتوا: <b>{sil.get('interested', 0)}</b>\n"
            f"{prr_icon} نسبة الرد بعد سماع السعر: <b>{prr:.0f}%</b>"
        )

    # ══ 5) سلّم المتابعات ══
    fu_total = sum(fu.get(n, 0) for n in (1, 2, 3, 4))
    fu_cv = data.get("fu_converted", 0)
    fu_rate = (fu_cv / fu_total * 100) if fu_total else 0
    S.append(
        f"🔔 <b>سلّم المتابعات الذكي</b>\n"
        f"#1 نكزة: <b>{fu.get(1, 0)}</b>  |  #2 قيمة: <b>{fu.get(2, 0)}</b>  |  "
        f"#3 خصم: <b>{fu.get(3, 0)}</b>  |  #4 آخر فرصة: <b>{fu.get(4, 0)}</b>\n"
        f"📤 الإجمالي: <b>{fu_total}</b>  →  ✅ تحوّلوا لطلب: <b>{fu_cv}</b>"
        + (f" (<b>{fu_rate:.0f}%</b>)" if fu_total else "")
    )

    # ══ 6) الفرص الضايعة ══
    lost_lines = [
        f"💸 <b>الفرص الضايعة</b>",
        f"مهتمين ما اشتروش: <b>{lost.get('interested_no_order', 0)}</b>",
        f"اعترضوا على السعر: <b>{lost.get('objections', 0)}</b>",
    ]
    gaps = lost.get("gap_products") or []
    weak = [g for g in gaps if g.get("conversion", 100) < 30][:2]
    if weak:
        lost_lines.append("\n⚠️ <b>محتاج مراجعة:</b>")
        for g in weak:
            lost_lines.append(
                f"• «{_esc(g['name'])[:26]}»: اتسأل {g['asked']} مرة → "
                f"باع {g['bought']} بس (<b>{g['conversion']:.0f}%</b>)")
    S.append("\n".join(lost_lines))

    # ══ 7) أداء الإعلانات ══
    ads = data.get("ads_performance", [])
    if ads:
        lines = ["📢 <b>أداء الإعلانات</b>"]
        for ad in ads[:3]:
            c = ad["conversion"]
            icon = "🟢" if c >= 20 else ("🟠" if c >= 8 else "🔴")
            lines.append(
                f"{icon} «{_esc(ad['title'])[:26]}»: {ad['convos']} محادثة → "
                f"{ad['orders']} طلب (<b>{c:.0f}%</b>)")
        S.append("\n".join(lines))

    # ══ 8) محتاج انتباهك ══
    complaints = data.get("complaints", 0)
    handoffs   = data.get("human_handoffs", 0)
    if complaints or handoffs:
        S.append(
            f"🚨 <b>محتاج انتباهك</b>\n"
            f"شكاوى: <b>{complaints}</b>   |   طلبوا موظف: <b>{handoffs}</b>"
        )

    # ══ 9) تحليل AI ══
    if loss_analysis and loss_analysis.get("breakdown"):
        lines = ["🧠 <b>تحليل AI: ليه العملاء ماشتروش؟</b>"]
        for item in loss_analysis["breakdown"][:4]:
            lines.append(f"• {_esc(item.get('reason',''))}: <b>{item.get('percent',0)}%</b>")
        if loss_analysis.get("suggestions"):
            lines.append("\n💡 <b>اقتراحات للتحسين:</b>")
            for s in loss_analysis["suggestions"][:3]:
                lines.append(f"← {_esc(s)}")
        S.append("\n".join(lines))

    S.append("━━━━━━━━━━━━━━━━━\n🤖 تقرير تلقائي من بوت المبيعات")
    return "\n\n".join(S)