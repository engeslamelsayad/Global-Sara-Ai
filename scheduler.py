"""
scheduler.py — المجدول المركزي للمهام الدورية

بيشغّل background thread واحد بيعمل:
1. معالجة رسائل ربط تليجرام (كل دقيقة)
2. إرسال التقرير الأسبوعي (كل سبت الساعة 9 صباحاً بتوقيت مصر)

التوقيت: مصر UTC+2 (بدون توقيت صيفي حالياً)، فـ 9 صباحاً محلي = 7 صباحاً UTC
"""

import threading
import time
from datetime import datetime, timedelta

import telegram_bot

# سبت = 5 في Python (Monday=0)، الساعة 9 صباحاً مصر = 7 UTC
REPORT_WEEKDAY = 5      # Saturday
REPORT_HOUR_UTC = 7     # 9 AM Egypt (UTC+2)

_scheduler_started = False
_last_report_date = None   # نمنع إرسال التقرير مرتين في نفس اليوم


def _collect_lost_samples(tenant, max_samples=12):
    """يجمع عينات محادثات فاشلة (اهتموا ومااشتروش) لتحليل الـ AI"""
    from bot_engine import list_tenant_states
    samples = []
    for st in list_tenant_states(tenant.id):
        if st.get("has_order") or st.get("platform") == "demo":
            continue
        if st.get("stage") not in ("INTERESTED", "OBJECTION", "INQUIRY"):
            continue
        history = st.get("history", [])
        if len(history) < 4:
            continue
        # نص مختصر من آخر 8 رسائل
        lines = []
        for h in history[-8:]:
            who = "عميل" if h.get("role") == "user" else "بوت"
            lines.append(f"{who}: {h.get('content','')[:120]}")
        samples.append("\n".join(lines))
        if len(samples) >= max_samples:
            break
    return samples


def _send_weekly_reports(app):
    """يبعت التقرير الأسبوعي لكل tenant مربوط بتليجرام"""
    from models import Tenant
    import analytics

    with app.app_context():
        tenants = Tenant.query.filter_by(
            telegram_enabled=True, is_active=True
        ).all()

        sent = 0
        for tenant in tenants:
            if not tenant.telegram_chat_id:
                continue
            try:
                data = analytics.get_tenant_analytics(tenant)

                # ── تحليل AI لأسباب فقدان البيع ──
                loss_analysis = None
                try:
                    import ai_assist
                    samples = _collect_lost_samples(tenant)
                    if samples:
                        bc = tenant.bot_config
                        dialect = bc.dialect if bc else "مصري"
                        loss_analysis = ai_assist.analyze_lost_conversations(
                            samples, dialect)
                except Exception as e:
                    print(f"⚠️ AI loss analysis failed for {tenant.slug}: {e}")

                report = telegram_bot.build_weekly_report(
                    tenant, data, loss_analysis=loss_analysis)
                if telegram_bot.send_message(tenant.telegram_chat_id, report):
                    sent += 1
                    print(f"📊 تقرير أسبوعي اتبعت لـ {tenant.slug}")
            except Exception as e:
                print(f"⚠️ فشل تقرير {tenant.slug}: {e}")

        print(f"✅ التقارير الأسبوعية: {sent}/{len(tenants)} اتبعت")


def _scheduler_loop(app):
    """الحلقة الرئيسية — بتشتغل طول عمر التطبيق"""
    global _last_report_date
    print("🕐 Scheduler started (Telegram + Smart Recovery followups)")

    loop_count = 0

    while True:
        try:
            now = datetime.utcnow()
            loop_count += 1

            # 1) معالجة رسائل ربط تليجرام (كل ~60 ثانية)
            if telegram_bot.is_configured():
                telegram_bot.process_link_messages(app)

            # 2) المتابعات الذكية (Smart Recovery) — كل 15 دقيقة
            if loop_count % 15 == 1:
                try:
                    import recovery
                    recovery.run_followups(app)
                except Exception as e:
                    print(f"⚠️ Recovery error: {e}")

            # 3) التقرير الأسبوعي — سبت 9ص مصر (7 UTC)
            is_report_time = (
                now.weekday() == REPORT_WEEKDAY
                and now.hour == REPORT_HOUR_UTC
            )
            today_str = now.strftime("%Y-%m-%d")
            if is_report_time and _last_report_date != today_str:
                if telegram_bot.is_configured():
                    print("📅 وقت التقرير الأسبوعي!")
                    _send_weekly_reports(app)
                _last_report_date = today_str

        except Exception as e:
            print(f"⚠️ Scheduler error: {e}")

        # ننام 60 ثانية بين كل دورة
        time.sleep(60)


def start_scheduler(app):
    """يبدأ الـ scheduler مرة واحدة فقط"""
    global _scheduler_started
    import os as _os
    if _os.environ.get("DISABLE_SCHEDULER"):
        return
    if _scheduler_started:
        return
    _scheduler_started = True

    thread = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True)
    thread.start()
