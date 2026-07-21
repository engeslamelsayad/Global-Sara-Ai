# -*- coding: utf-8 -*-
"""
salary_campaign.py — حملة "يوم المرتبات" 💰

بتبعت عرض خصم للعملاء المهتمين اللي ماشتروش، في أيام محددة من الشهر
(افتراضياً يوم 25 ويوم 1 — مواعيد نزول المرتبات في مصر).

مبادئ التصميم:
- كل حملة بتشتغل في Thread منفصل عشان الـ rate limiting (sleep بين الرسائل)
  مايعطلش حلقة الـ scheduler الرئيسية اللي بتلف كل 60 ثانية.
- Rate limiting: msgs_per_minute لكل tenant → sleep = 60 / rate بين كل رسالة.
- حماية من التكرار: فاصل MIN_DAYS_BETWEEN_CAMPAIGNS يوم بين حملتين لنفس العميل
  (عشان اللي خد رسالة يوم 25 مياخدش تاني يوم 1 بعدها بأسبوع)،
  وفاصل 24 ساعة مع سلّم المتابعات العادي.
- Circuit breaker: لو Meta رفضت 5 إرسالات ورا بعض (نافذة الـ 24 ساعة مقفولة)،
  الحملة بتقف فوراً بدل ما نضرب في حيطة ونتعلّم فلاج سبام.
  ⚠️ عشان كده بنبعت لـ Graph API مباشرة هنا (مش عبر bot_engine.send_message)
  لأن الأخيرة بتبلع الأخطاء ومش بترجّع نجاح/فشل.
- last_run_date على مستوى الحملة نفسها → الحملة بتتبعت مرة واحدة بس في اليوم
  حتى لو السيرفر اتعمله restart.
"""

import time
import threading
import requests
from datetime import datetime, timedelta

# ── إعدادات حماية عامة (على مستوى النظام كله) ──────────────────────────
MAX_SENDS_PER_CAMPAIGN     = 300   # حد أقصى للحملة الواحدة لكل tenant
MIN_DAYS_BETWEEN_CAMPAIGNS = 20    # فاصل إجباري بين حملتين لنفس العميل
MAX_LEAD_AGE_DAYS          = 30    # بس اللي اتفاعلوا خلال الشهر الأخير
MIN_HOURS_AFTER_FOLLOWUP   = 24    # ماتبعتش لو خد follow-up عادي من قريب
MAX_CONSECUTIVE_FAILURES   = 5     # circuit breaker
EGYPT_UTC_OFFSET           = 2     # توقيت مصر (نفس افتراض التقرير الأسبوعي)

# حماية من تشغيل مزدوج لنفس الـ tenant (لو الحملة لسه شغالة)
_running_tenants = set()
_running_lock = threading.Lock()


# =====================================================================
# نقطة الدخول — بتتنادى من scheduler.py كل دورة (60 ثانية)
# =====================================================================
def check_and_run(app):
    """
    يشوف مين من الـ tenants عنده حملة مستحقة النهارده في الساعة دي،
    ويشغّلها في thread منفصل. رخيصة جداً لو مفيش حاجة مستحقة.
    """
    from models import db, SalaryCampaign, Tenant

    egypt_now = datetime.utcnow() + timedelta(hours=EGYPT_UTC_OFFSET)
    today_str = egypt_now.strftime("%Y-%m-%d")
    today_day = egypt_now.day
    this_hour = egypt_now.hour

    with app.app_context():
        campaigns = (SalaryCampaign.query
                     .filter_by(is_active=True)
                     .join(Tenant, Tenant.id == SalaryCampaign.tenant_id)
                     .filter(Tenant.is_active.is_(True))
                     .all())

        for camp in campaigns:
            # اتبعتت النهارده بالفعل؟ (بيصمد قدام الـ restarts)
            if camp.last_run_date == today_str:
                continue

            # النهارده من أيام الحملة؟ ("25,1" → [25, 1])
            try:
                days = [int(d) for d in (camp.days_of_month or "").split(",") if d.strip()]
            except ValueError:
                days = [25, 1]
            if today_day not in days:
                continue

            # وصلنا لساعة الإرسال؟ (>= مش == عشان لو السيرفر كان نايم وقتها)
            if this_hour < (camp.send_hour if camp.send_hour is not None else 14):
                continue

            # علّم إنها اتبعتت النهارده *قبل* التشغيل — عشان لو الدورة
            # الجاية جت والـ thread لسه شغال مانبعتش مرتين
            camp.last_run_date = today_str
            db.session.commit()

            with _running_lock:
                if camp.tenant_id in _running_tenants:
                    continue
                _running_tenants.add(camp.tenant_id)

            print(f"💰 Salary campaign due for tenant {camp.tenant_id} "
                  f"(day {today_day}, discount {camp.discount_percent}%)")
            threading.Thread(
                target=_run_campaign_thread,
                args=(app, camp.tenant_id, camp.id),
                daemon=True,
            ).start()


def _run_campaign_thread(app, tenant_id, campaign_id):
    """غلاف الـ thread — بيضمن تنضيف _running_tenants مهما حصل"""
    try:
        _run_campaign(app, tenant_id, campaign_id)
    except Exception as e:
        print(f"⚠️ Salary campaign fatal error (tenant {tenant_id}): {e}")
    finally:
        with _running_lock:
            _running_tenants.discard(tenant_id)


# =====================================================================
# تشغيل الحملة الفعلي — مع rate limiting
# =====================================================================
def _run_campaign(app, tenant_id, campaign_id):
    from models import db, SalaryCampaign
    from bot_engine import (list_tenant_states_with_ids, save_state,
                            get_tenant_for_page)
    from recovery import _pick_product   # نفس منطق اختيار المنتج

    now = time.time()

    with app.app_context():
        camp = SalaryCampaign.query.get(campaign_id)
        if not camp:
            return

        rate = max(1, min(camp.msgs_per_minute or 10, 60))
        sleep_between = 60.0 / rate

        sent = 0
        consecutive_failures = 0

        for sender_id, state in list_tenant_states_with_ids(tenant_id):
            if sent >= MAX_SENDS_PER_CAMPAIGN:
                print(f"💰 Campaign cap reached ({MAX_SENDS_PER_CAMPAIGN}) — stopping")
                break

            if not _is_eligible(state, now):
                continue

            bundle = get_tenant_for_page(state["page_id"])
            if not bundle:
                continue

            product = _pick_product(state, bundle["products"])
            msg = _build_message(camp, product)

            ok = _send_with_status(bundle, sender_id, msg, state["page_id"])
            if not ok:
                consecutive_failures += 1
                print(f"⚠️ Salary send failed for {sender_id[:12]}... "
                      f"({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print("🛑 Circuit breaker: too many failures — aborting campaign")
                    break
                continue
            consecutive_failures = 0

            # تحديث حالة العميل
            state["last_salary_campaign_time"] = now
            state["last_followup_time"] = now          # بيأجّل السلّم العادي برضه
            if camp.discount_percent:
                state["has_discount"] = camp.discount_percent
            save_state(tenant_id, sender_id, state)

            sent += 1

            # ⏱ Rate limiting — نضمن مانتخطاش msgs_per_minute
            time.sleep(sleep_between)

        # إحصائيات الحملة
        camp = SalaryCampaign.query.get(campaign_id)
        if camp:
            camp.last_run_sent_count = sent
            db.session.commit()

        print(f"💰 Salary campaign done for tenant {tenant_id}: {sent} sent")


# =====================================================================
# الإرسال — مباشرة لـ Graph API مع فحص النجاح/الفشل
# (bot_engine.send_message بتبلع الأخطاء — فالـ circuit breaker
#  مش هيشوف الرفضات لو استخدمناها)
# =====================================================================
def _send_with_status(bundle, sender_id, text, page_id):
    """يبعت الرسالة ويرجّع True/False — عشان الـ circuit breaker يشتغل فعلاً"""
    page = bundle["pages"].get(page_id)
    if not page or not page.access_token:
        print(f"❌ No access token for page {page_id}")
        return False
    try:
        r = requests.post(
            "https://graph.facebook.com/v18.0/me/messages",
            params={"access_token": page.access_token},
            json={"recipient": {"id": sender_id}, "message": {"text": text}},
            timeout=10,
        )
        if r.status_code != 200:
            # أشهر سبب: خارج نافذة الـ 24 ساعة (error code 10 / subcode 2018278)
            print(f"⚠️ Meta {r.status_code}: {r.text[:150]}")
            return False
        return True
    except Exception as e:
        print(f"⚠️ Salary send error: {e}")
        return False


# =====================================================================
# الأهلية
# =====================================================================
def _is_eligible(state, now):
    """مين يستحق رسالة يوم المرتبات؟"""
    # نفس استبعادات السلّم العادي
    if (state.get("has_order") or state.get("is_human_handoff")
            or state.get("has_complaint")
            or state.get("platform") == "demo"
            or not state.get("page_id")
            or state.get("page_id") == "demo"
            or not state.get("history")):
        return False

    # اتفاعل خلال الشهر الأخير؟ (lead مش بارد)
    last_msg = state.get("last_message") or state.get("created_at") or 0
    if not last_msg:
        return False
    if (now - last_msg) / 86400 > MAX_LEAD_AGE_DAYS:
        return False

    # ماخدش حملة مرتبات من قريب (يوم 25 → مياخدش تاني يوم 1)
    last_camp = state.get("last_salary_campaign_time") or 0
    if last_camp and (now - last_camp) / 86400 < MIN_DAYS_BETWEEN_CAMPAIGNS:
        return False

    # ماخدش follow-up عادي خلال آخر 24 ساعة (مانزنّش عليه مرتين في يوم)
    last_fu = state.get("last_followup_time") or 0
    if last_fu and (now - last_fu) / 3600 < MIN_HOURS_AFTER_FOLLOWUP:
        return False

    return True


# =====================================================================
# بناء الرسالة
# =====================================================================
DEFAULT_MESSAGE = (
    "أهلاً بيك يا فندم 😊 بمناسبة أول الشهر عملنا لحضرتك عرض خاص: "
    "خصم {discount}% على {product} اللي كنت بتسأل عنه 🎁\n"
    "العرض لفترة محدودة — والدفع عند الاستلام زي ما هو، "
    "يعني بتشوف المنتج بعينك الأول. تحب أحجزهولك قبل ما العرض يخلص؟ 💙"
)


def _build_message(camp, product):
    """يبني الرسالة — بيدعم {product} و {discount} زي مراحل السلّم"""
    template = (camp.message_text or "").strip() or DEFAULT_MESSAGE

    pname = f"«{product.name}»" if (product and product.name) else "المنتج"

    return (template
            .replace("{product}", pname)
            .replace("{discount}", str(camp.discount_percent or 0)))
