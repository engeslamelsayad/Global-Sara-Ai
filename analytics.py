"""
analytics.py — لوحة تحليلات لكل tenant، مبنية ديناميكياً من DB + Redis

المقاييس الجديدة في هذه النسخة:
  - orders_last_24h: عداد دوّار (rolling window) لعدد الطلبات في آخر 24 ساعة بالظبط
    (مش "اليوم التقويمي" — بيتحرك مع الوقت لحظة بلحظة)
  - orders_last_7d / orders_last_30d: لمقارنة الفترات الزمنية المختلفة
"""

import os
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Blueprint, request

from models import Tenant, Order, Product
from bot_engine import list_tenant_states

analytics_bp = Blueprint("analytics", __name__)

ANALYTICS_KEY = os.environ.get("ANALYTICS_KEY", "changeme")


# =====================================================================
# COMPUTE METRICS
# =====================================================================
def get_tenant_analytics(tenant):
    now = datetime.utcnow()

    # ── الطلبات: من الداتابيز مباشرة (مصدر الحقيقة) ──
    all_orders = Order.query.filter_by(tenant_id=tenant.id).order_by(Order.created_at.desc()).all()

    orders_24h = [o for o in all_orders if (now - o.created_at) <= timedelta(hours=24)]
    orders_7d  = [o for o in all_orders if (now - o.created_at) <= timedelta(days=7)]
    orders_30d = [o for o in all_orders if (now - o.created_at) <= timedelta(days=30)]

    # ── normalization: نجمّع تنويعات اسم المنتج تحت الاسم العام للـ tenant ──
    # نبني خريطة من كلمات كل منتج → اسمه الرسمي في الداتابيز
    tenant_products = Product.query.filter_by(tenant_id=tenant.id).all()

    def normalize_product(raw_name):
        """يطابق اسم الطلب مع منتج الـ tenant الفعلي عبر الكلمات المفتاحية أو الاسم"""
        if not raw_name:
            return "غير محدد"
        text = raw_name.strip().lower()
        best_match = None
        # 1) الأولوية للكلمات المفتاحية (أدق في تجميع التنويعات)
        for p in tenant_products:
            if p.keywords:
                for kw in p.keywords.split(","):
                    kw = kw.strip().lower()
                    if kw and len(kw) >= 3 and kw in text:
                        return p.name
        # 2) تطابق مع الاسم الرسمي كامل
        for p in tenant_products:
            if p.name and p.name.strip().lower() in text:
                return p.name
        # 3) تطابق جزئي: أول كلمتين مميزتين من اسم المنتج موجودين في الطلب
        for p in tenant_products:
            if not p.name:
                continue
            # نشيل الأقواس والكلمات العامة ونقارن الكلمات الأساسية
            core = p.name.split("(")[0].strip().lower()
            core_words = [w for w in core.split() if len(w) >= 3
                          and w not in ("كريم", "بخاخ", "قلم", "زيت", "سيروم", "مجموعة")]
            if core_words and all(w in text for w in core_words):
                return p.name
        return raw_name.strip()[:40]

    orders_by_product = defaultdict(int)
    for o in all_orders:
        orders_by_product[normalize_product(o.product_name)] += 1

    # ⭐ تقسيم طلبات آخر 24 ساعة حسب المنتج (بالاسم العام الموحّد)
    orders_24h_by_product = defaultdict(int)
    for o in orders_24h:
        orders_24h_by_product[normalize_product(o.product_name)] += 1
    orders_24h_by_product = dict(
        sorted(orders_24h_by_product.items(), key=lambda x: x[1], reverse=True)
    )

    # ── حالات المحادثات: من Redis (حالة لحظية) ──
    states = list_tenant_states(tenant.id)

    total_conversations = len(states)
    active_last_hour = sum(
        1 for s in states
        if (now.timestamp() - s.get("last_message", 0)) < 3600
    )
    complaints       = sum(1 for s in states if s.get("has_complaint"))
    human_handoffs   = sum(1 for s in states if s.get("is_human_handoff"))

    funnel_counts = defaultdict(int)
    for s in states:
        funnel_counts[s.get("stage", "NEW")] += 1

    product_inquiries = defaultdict(int)
    for s in states:
        for pk in s.get("products_asked", []):
            product_inquiries[pk] += 1

    # ── Follow-up stats من الحالات ──
    fu1_sent = sum(1 for s in states if 1 in s.get("followup_stages_sent", []))
    fu2_sent = sum(1 for s in states if 2 in s.get("followup_stages_sent", []))
    fu_converted = sum(
        1 for s in states
        if s.get("has_order") and s.get("followup_stages_sent")
    )

    conversion_rate = (len(all_orders) / total_conversations * 100) if total_conversations else 0

    # ══ لوحة الفرص الضايعة ══════════════════════════════
    # تحليل يوضّح للتاجر فين بيخسر مبيعات
    lost = _compute_lost_opportunities(states, tenant_products, normalize_product)

    # ══ أداء الإعلانات ══════════════════════════════════
    # ربط مصدر الإعلان بالتحويل — ذهب للتاجر: يعرف أنهي إعلان بيبيع فعلاً
    ads_map = defaultdict(lambda: {"convos": 0, "orders": 0})
    for s in states:
        title = (s.get("source_ad_title") or "").strip()
        if not title:
            continue
        ads_map[title]["convos"] += 1
        if s.get("has_order"):
            ads_map[title]["orders"] += 1
    ads_performance = [
        {
            "title": t,
            "convos": v["convos"],
            "orders": v["orders"],
            "conversion": (v["orders"] / v["convos"] * 100) if v["convos"] else 0,
        }
        for t, v in ads_map.items()
    ]
    # ترتيب: الأعلى محادثات أولاً (الأهم للمراجعة)
    ads_performance.sort(key=lambda a: a["convos"], reverse=True)

    return {
        "total_conversations": total_conversations,
        "active_last_hour": active_last_hour,
        "total_orders": len(all_orders),
        "orders_last_24h": len(orders_24h),     # ⭐ العداد الجديد المطلوب
        "orders_last_7d": len(orders_7d),
        "orders_last_30d": len(orders_30d),
        "conversion_rate": round(conversion_rate, 1),
        "complaints": complaints,
        "human_handoffs": human_handoffs,
        "fu1_sent": fu1_sent,
        "fu2_sent": fu2_sent,
        "fu_converted": fu_converted,
        "funnel_counts": dict(funnel_counts),
        "orders_by_product": dict(orders_by_product),
        "orders_24h_by_product": orders_24h_by_product,   # ⭐ الجديد
        "product_inquiries": dict(product_inquiries),
        "recent_orders": all_orders[:15],
        "lost_opportunities": lost,   # ⭐ الفرص الضايعة
        "ads_performance": ads_performance,   # ⭐ أداء الإعلانات
        "updated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _compute_lost_opportunities(states, tenant_products, normalize_fn):
    """
    يحلّل المحادثات ويكتشف الفرص الضايعة:
    - عملاء اهتموا بمنتج وما اشتروش
    - عملاء اعترضوا على السعر
    - منتجات بتتسأل كتير وبتتباع قليل
    """
    # خريطة product_key → اسم المنتج
    key_to_name = {p.product_key: p.name for p in tenant_products}

    interested_no_order = 0    # وصلوا INTERESTED وما عملوش أوردر
    objections          = 0    # اعترضوا (غالي/مش متأكد/بعدين)
    abandoned_at_data   = 0    # كانوا بيسجلوا وسابوا

    # لكل منتج: كام سأل عنه وكام اشتراه
    product_asked  = defaultdict(int)
    product_bought = defaultdict(int)

    for s in states:
        stage    = s.get("stage", "NEW")
        has_order = s.get("has_order", False)
        asked    = s.get("products_asked", [])

        # عملاء مهتمين بس ما اشتروش
        if stage in ("INTERESTED", "OBJECTION") and not has_order:
            interested_no_order += 1
        if stage == "OBJECTION":
            objections += 1

        # منتجات: سؤال مقابل شراء
        for pk in asked:
            product_asked[pk] += 1
        if has_order:
            # نحسب المنتج اللي اتطلب (آخر منتج في القائمة غالباً)
            if asked:
                product_bought[asked[-1]] += 1

    # المنتجات اللي بتتسأل كتير وبتتباع قليل (أكبر فجوة)
    gap_products = []
    for pk, asked_count in product_asked.items():
        bought = product_bought.get(pk, 0)
        if asked_count >= 2:   # على الأقل سؤالين عشان يبقى ذو دلالة
            gap = asked_count - bought
            conv = (bought / asked_count * 100) if asked_count else 0
            gap_products.append({
                "name": key_to_name.get(pk, pk),
                "asked": asked_count,
                "bought": bought,
                "conversion": round(conv, 0),
                "gap": gap,
            })
    # الأسوأ تحويلاً أول (المنتج اللي بيتسأل عنه وما بيتباعش)
    gap_products.sort(key=lambda x: (x["conversion"], -x["asked"]))

    return {
        "interested_no_order": interested_no_order,
        "objections": objections,
        "gap_products": gap_products[:8],
    }


# =====================================================================
# HTML RENDERING
# =====================================================================
_CSS = """
body{font-family:Segoe UI,Tahoma,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:20px;direction:rtl}
h1{color:#7c3aed;text-align:center;border-bottom:2px solid #7c3aed;padding-bottom:10px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.card{background:#1e293b;border-radius:12px;padding:16px;border:1px solid #334155;text-align:center}
.card h2{color:#94a3b8;font-size:12px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.5px}
.num{font-size:30px;font-weight:700;color:#7c3aed}
.green{color:#10b981!important}.yellow{color:#f59e0b!important}.red{color:#ef4444!important}.blue{color:#3b82f6!important}
.sec{background:#1e293b;border-radius:12px;padding:16px;margin:10px 0;border:1px solid #334155}
.sec h3{color:#7c3aed;border-bottom:1px solid #334155;padding-bottom:6px;margin-top:0}
.bar-row{display:flex;align-items:center;margin:5px 0;gap:8px}
.lbl{width:170px;font-size:12px;color:#94a3b8;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{background:#334155;border-radius:4px;flex:1;height:18px}
.fill{background:linear-gradient(90deg,#7c3aed,#2563eb);height:100%;border-radius:4px}
.val{width:32px;font-size:12px;color:#e2e8f0;text-align:left}
.stage{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;margin:4px 0;background:#0f172a;border-radius:8px;font-size:13px}
.order-row{display:flex;justify-content:space-between;padding:6px 10px;font-size:12px;border-bottom:1px solid #334155}
footer{font-size:11px;color:#475569;text-align:center;margin-top:20px}
"""


def _bars(d, top=10):
    items = sorted(d.items(), key=lambda x: x[1], reverse=True)[:top]
    mx = max((v for _, v in items), default=1)
    if not items:
        return "<p style='color:#475569;font-size:13px'>لا توجد بيانات بعد</p>"
    return "".join(
        f'<div class="bar-row"><div class="lbl">{k[:24]}</div>'
        f'<div class="bar"><div class="fill" style="width:{int(v/mx*100)}%"></div></div>'
        f'<div class="val">{v}</div></div>'
        for k, v in items
    )


def build_analytics_html(tenant, data):
    funnel_ar = {
        "NEW": "جديد", "INQUIRY": "استفسار", "INTERESTED": "مهتم",
        "OBJECTION": "اعتراض", "ORDERED": "أوردر مسجل",
        "HUMAN_NEEDED": "يحتاج موظف", "COMPLAINT": "شكوى",
    }
    funnel_html = "".join(
        f'<div class="stage"><span>{ar}</span><span style="font-weight:700;color:#7c3aed">'
        f'{data["funnel_counts"].get(k, 0)}</span></div>'
        for k, ar in funnel_ar.items()
    )

    orders_html = "".join(
        f'<div class="order-row"><span>{o.customer_name or "—"} | {o.product_name or "—"}</span>'
        f'<span style="color:#94a3b8">{o.created_at.strftime("%m-%d %H:%M")}</span></div>'
        for o in data["recent_orders"]
    ) or "<p style='color:#475569;font-size:13px'>لا توجد طلبات بعد</p>"

    # ── HTML الفرص الضايعة ──
    lost = data["lost_opportunities"]
    if lost["gap_products"]:
        gap_rows = "".join(
            f'<div class="bar-row">'
            f'<div class="lbl" style="width:200px">{p["name"]}</div>'
            f'<div style="flex:1;font-size:13px;color:#475569">'
            f'سأل <b>{p["asked"]}</b> · اشترى <b style="color:#10b981">{p["bought"]}</b> · '
            f'تحويل <b style="color:{"#10b981" if p["conversion"]>=30 else "#ef4444"}">{p["conversion"]:.0f}%</b>'
            f'</div></div>'
            for p in lost["gap_products"]
        )
    else:
        gap_rows = "<p style='color:#475569;font-size:13px'>لسه مفيش بيانات كافية</p>"

    lost_html = f"""
    <div class="grid" style="margin-bottom:16px">
      <div class="card" style="border:2px solid #ef4444">
        <h2>مهتمين ما اشتروش</h2>
        <div class="num red">{lost['interested_no_order']}</div>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0 0">عملاء وصلوا لمرحلة الاهتمام وما أكملوش</p>
      </div>
      <div class="card" style="border:2px solid #f97316">
        <h2>اعترضوا على السعر</h2>
        <div class="num" style="color:#f97316">{lost['objections']}</div>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0 0">قالوا غالي أو مش متأكد أو بعدين</p>
      </div>
    </div>
    <div style="font-size:14px;font-weight:700;margin:16px 0 8px;color:#334155">
      📉 المنتجات: سؤال مقابل شراء (الأكبر فجوة أول)
    </div>
    {gap_rows}
    """

    # ── HTML أداء الإعلانات ──
    ads = data.get("ads_performance", [])
    if ads:
        ads_rows = "".join(
            f'<div class="bar-row">'
            f'<div class="lbl" style="width:220px" title="{a["title"]}">{a["title"][:35]}</div>'
            f'<div style="flex:1;font-size:13px;color:#475569">'
            f'<b>{a["convos"]}</b> محادثة · <b style="color:#10b981">{a["orders"]}</b> طلب · '
            f'تحويل <b style="color:{"#10b981" if a["conversion"]>=20 else "#ef4444"}">{a["conversion"]:.0f}%</b>'
            f'</div></div>'
            for a in ads[:10]
        )
        ads_html = f"""
<div class="sec" style="border:2px solid #6D28D9">
  <h3>📢 أداء الإعلانات — أنهي إعلان بيبيع فعلاً</h3>
  <p style="font-size:12px;color:#94a3b8;margin:0 0 10px">
    إعلان جايب محادثات كتير بتحويل ضعيف = بيهدر فلوسك. إعلان تحويله عالي = زوّد ميزانيته.
  </p>
  {ads_rows}
</div>"""
    else:
        ads_html = """
<div class="sec" style="border:2px solid #6D28D9">
  <h3>📢 أداء الإعلانات — أنهي إعلان بيبيع فعلاً</h3>
  <p style="font-size:13px;color:#64748b;margin:0">
    لسه مفيش بيانات — القسم ده بيتملأ تلقائياً لما عملاء يدخلوا من إعلانات
    Click-to-Messenger. هتشوف هنا: كل إعلان جاب كام محادثة وكام طلب ونسبة تحويله.
    <br><span style="color:#94a3b8;font-size:12px">💡 نصيحة: خلّي عنوان الإعلان (ad title) فيه اسم المنتج عشان البوت يتعرّف عليه تلقائياً.</span>
  </p>
</div>"""

    return f"""<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{tenant.business_name} — Analytics</title><style>{_CSS}</style>
<script>setTimeout(()=>location.reload(), 60000)</script>
</head><body>
<h1>📊 لوحة تحليلات {tenant.business_name}</h1>

<div class="grid">
  <div class="card"><h2>طلبات آخر 24 ساعة</h2><div class="num green">{data['orders_last_24h']}</div></div>
  <div class="card"><h2>طلبات آخر 7 أيام</h2><div class="num blue">{data['orders_last_7d']}</div></div>
  <div class="card"><h2>طلبات آخر 30 يوم</h2><div class="num blue">{data['orders_last_30d']}</div></div>
  <div class="card"><h2>إجمالي الطلبات</h2><div class="num">{data['total_orders']}</div></div>
  <div class="card"><h2>المحادثات</h2><div class="num">{data['total_conversations']}</div></div>
  <div class="card"><h2>معدل التحويل</h2><div class="num green">{data['conversion_rate']}%</div></div>
  <div class="card"><h2>نشطين (ساعة)</h2><div class="num">{data['active_last_hour']}</div></div>
  <div class="card"><h2>طلبوا موظف</h2><div class="num red">{data['human_handoffs']}</div></div>
  <div class="card"><h2>شكاوى</h2><div class="num red">{data['complaints']}</div></div>
</div>

<div class="sec" style="border:2px solid #ef4444">
  <h3>💸 الفرص الضايعة — فين بتخسر مبيعات</h3>
  {lost_html}
</div>
{ads_html}

<div class="sec"><h3>📬 Follow-up Stats</h3>
  <div class="grid">
    <div class="card"><h2>Follow-up #1 أُرسل</h2><div class="num yellow">{data['fu1_sent']}</div></div>
    <div class="card"><h2>Follow-up #2 أُرسل</h2><div class="num" style="color:#f97316">{data['fu2_sent']}</div></div>
    <div class="card"><h2>تحوّلوا لطلب</h2><div class="num green">{data['fu_converted']}</div></div>
  </div>
</div>

<div class="sec" style="border:2px solid #10b981">
  <h3>🔥 طلبات آخر 24 ساعة حسب المنتج ({data['orders_last_24h']} طلب)</h3>
  {_bars(data['orders_24h_by_product'])}
</div>

<div class="sec"><h3>🔄 Funnel العملاء</h3>{funnel_html}</div>
<div class="sec"><h3>🏆 أكثر المنتجات طلبات (إجمالي)</h3>{_bars(data['orders_by_product'])}</div>
<div class="sec"><h3>🔍 أكثر المنتجات استفساراً</h3>{_bars(data['product_inquiries'])}</div>
<div class="sec"><h3>🧾 آخر الطلبات</h3>{orders_html}</div>


<footer>آخر تحديث: {data['updated_at']} | يتجدد تلقائياً كل دقيقة</footer>
</body></html>"""


# =====================================================================
# ROUTE
# =====================================================================
@analytics_bp.route("/analytics/<tenant_slug>")
def tenant_analytics(tenant_slug):
    from flask_login import current_user
    from models import User

    # ── طريقة 1: مفتاح API عام (للأدمن أو الوصول الخارجي) ──
    if request.args.get("key") == ANALYTICS_KEY:
        pass  # مسموح

    # ── طريقة 2: مستخدم مسجّل دخوله ينظر في تحليلات الـ tenant بتاعه ──
    elif current_user and current_user.is_authenticated:
        # تأكد إن الـ tenant slug بتاع الـ user هو نفسه المطلوب
        from models import Tenant as T
        user_tenant = T.query.get(current_user.tenant_id)
        if not user_tenant or user_tenant.slug != tenant_slug:
            return "غير مصرح — يمكنك فقط عرض تحليلات شركتك", 403
        # مسموح — اليوزر المسجّل بيشوف تحليلاته

    else:
        return "Unauthorized — سجّل دخولك أو أضف ?key=ANALYTICS_KEY", 403

    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if not tenant:
        return f"Tenant '{tenant_slug}' not found", 404

    data = get_tenant_analytics(tenant)
    return build_analytics_html(tenant, data), 200, {"Content-Type": "text/html; charset=utf-8"}
