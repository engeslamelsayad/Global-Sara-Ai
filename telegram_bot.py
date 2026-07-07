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


def send_message(chat_id, text, parse_mode="HTML"):
    """يبعت رسالة لمحادثة تليجرام"""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return False
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


def build_weekly_report(tenant, data, loss_analysis=None):
    """يبني نص التقرير الأسبوعي بصيغة HTML لتليجرام"""
    lost = data.get("lost_opportunities", {})

    # أفضل منتج مبيعاً
    top_products = sorted(
        data.get("orders_by_product", {}).items(),
        key=lambda x: x[1], reverse=True
    )
    top_line = ""
    if top_products:
        name, cnt = top_products[0]
        top_line = f"\n🏆 أكتر منتج مبيعاً: <b>{name}</b> ({cnt} طلب)"

    # فجوة أكبر منتج (فرصة ضايعة)
    gap_line = ""
    if lost.get("gap_products"):
        worst = lost["gap_products"][0]
        if worst["conversion"] < 30:
            gap_line = (
                f"\n\n⚠️ <b>فرصة للتحسين:</b>\n"
                f"منتج «{worst['name']}» اتسأل عنه {worst['asked']} مرة "
                f"واتباع {worst['bought']} بس (تحويل {worst['conversion']:.0f}%)"
            )

    # ── تحليل AI لأسباب فقدان البيع ──
    ai_section = ""
    if loss_analysis and loss_analysis.get("breakdown"):
        lines = ["\n\n🧠 <b>تحليل AI: ليه العملاء ماشتروش؟</b>"]
        for item in loss_analysis["breakdown"][:4]:
            lines.append(f"• {item.get('reason','')}: <b>{item.get('percent',0)}%</b>")
        if loss_analysis.get("suggestions"):
            lines.append("\n💡 <b>اقتراحات للتحسين:</b>")
            for s in loss_analysis["suggestions"][:3]:
                lines.append(f"← {s}")
        ai_section = "\n".join(lines)

    # ── أداء الإعلانات ──
    ads_section = ""
    ads = data.get("ads_performance", [])
    if ads:
        lines = ["\n\n📢 <b>أداء الإعلانات:</b>"]
        for ad in ads[:3]:
            lines.append(
                f"• «{ad['title'][:30]}»: {ad['convos']} محادثة → "
                f"{ad['orders']} طلب (تحويل <b>{ad['conversion']:.0f}%</b>)")
        ads_section = "\n".join(lines)

    report = f"""📊 <b>التقرير الأسبوعي — {tenant.business_name}</b>
━━━━━━━━━━━━━━━━━

🗓 <b>آخر 7 أيام:</b>
💬 محادثات جديدة: <b>{data.get('total_conversations', 0)}</b>
🛒 طلبات: <b>{data.get('orders_last_7d', 0)}</b>
📈 معدل التحويل: <b>{data.get('conversion_rate', 0)}%</b>{top_line}

🔔 <b>المتابعات (Follow-up):</b>
أُرسلت: {data.get('fu1_sent', 0) + data.get('fu2_sent', 0)} | تحوّلت لطلب: <b>{data.get('fu_converted', 0)}</b>

💸 <b>الفرص الضايعة:</b>
مهتمين ما اشتروش: <b>{lost.get('interested_no_order', 0)}</b>
اعترضوا على السعر: <b>{lost.get('objections', 0)}</b>{gap_line}{ai_section}{ads_section}

━━━━━━━━━━━━━━━━━
🤖 تقرير تلقائي من بوت المبيعات"""

    return report
