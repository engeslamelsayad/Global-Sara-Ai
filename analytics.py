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
def get_tenant_analytics(tenant, date_from=None, date_to=None):
    """
    date_from / date_to: timestamps (ثواني) لفلترة الفترة. None = بلا حد.
    الطلبات بتتفلتر بـ created_at (دقيق) والمحادثات بـ last_message.
    """
    now = datetime.utcnow()

    # ── الطلبات: من الداتابيز مباشرة (مصدر الحقيقة) ──
    _oq = Order.query.filter_by(tenant_id=tenant.id)
    if date_from:
        _oq = _oq.filter(Order.created_at >= datetime.utcfromtimestamp(date_from))
    if date_to:
        _oq = _oq.filter(Order.created_at <= datetime.utcfromtimestamp(date_to))
    all_orders = _oq.order_by(Order.created_at.desc()).all()

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
    # فلتر المحادثات حسب آخر نشاط (لو الفترة محددة)
    if date_from or date_to:
        def _in_range(s):
            lm = s.get("last_message") or 0
            if date_from and lm < date_from:
                return False
            if date_to and lm > date_to:
                return False
            return True
        states = [s for s in states if _in_range(s)]

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

    # معدل التحويل: من نفس مجموعة المحادثات (البسط والمقام من نفس النافذة الزمنية).
    # مهم: حالات Redis بتنتهي بعد 30 يوم بينما جدول Orders دائم — لو قسمنا
    # "كل طلبات التاريخ ÷ محادثات آخر 30 يوم" المعدل هيتضخم مع الوقت (ممكن يعدي 100%).
    convos_with_order = sum(1 for s in states if s.get("has_order"))
    conversion_rate = (convos_with_order / total_conversations * 100) if total_conversations else 0

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

    # ══ رؤى المنتجات — أداء واعتراضات كل منتج ═══════════════
    # بدل قراءة مئات المحادثات: لكل منتج، كام عميل اهتم، كام اعترض وعلى إيه،
    # كام طلب فعلاً — بنسب واضحة
    key_to_name = {p.product_key: p.name for p in tenant_products}
    product_insights = {}

    def _pi(key):
        if key not in product_insights:
            product_insights[key] = {
                "key": key,
                "name": key_to_name.get(key, key),
                "asked": 0,          # محادثات سألت عن المنتج
                "orders": 0,         # منها انتهت بطلب
                "interested_now": 0, # حالياً في مرحلة اهتمام (فرصة متابعة)
                "objecting_now": 0,  # حالياً معترضين
                "expensive": 0,      # اعتراضات "غالي"
                "unsure": 0,         # اعتراضات "مش متأكد"
                "later": 0,          # اعتراضات "بعدين/هفكر"
                "price_silent": 0,   # شافوا السعر وسكتوا
            }
        return product_insights[key]

    import time as _time
    _now_ts = _time.time()
    # buckets الصمت (نقاط موت المحادثات)
    silent_after_price = 0
    silent_after_obj   = 0
    silent_first_msg   = 0
    silent_interested  = 0
    price_quoted_total = 0
    replied_after_price = 0

    for s in states:
        if s.get("platform") == "demo":
            continue
        asked = set(s.get("products_asked", []))
        for key in asked:
            rec = _pi(key)
            rec["asked"] += 1
            if s.get("has_order"):
                rec["orders"] += 1
            stage = s.get("stage", "")
            if stage in ("INTERESTED", "INQUIRY") and not s.get("has_order"):
                rec["interested_now"] += 1
            elif stage == "OBJECTION" and not s.get("has_order"):
                rec["objecting_now"] += 1

        # ── فين العملاء بيسكتوا؟ ──
        if not s.get("has_order") and not s.get("is_human_handoff"):
            if s.get("price_quoted"):
                price_quoted_total += 1
                if s.get("last_message", 0) > s.get("price_quoted_time", 0):
                    replied_after_price += 1
            _silent = (_now_ts - s.get("last_message", _now_ts)) >= 6 * 3600
            _price_stuck = (s.get("price_quoted")
                            and s.get("last_message", 0) <= s.get("price_quoted_time", 0))
            if _silent:
                if _price_stuck:
                    silent_after_price += 1
                    _pq = s.get("price_quoted_product")
                    if _pq:
                        _pi(_pq)["price_silent"] += 1
                elif s.get("stage") == "OBJECTION":
                    silent_after_obj += 1
                elif len(s.get("history", [])) <= 2:
                    silent_first_msg += 1
                elif s.get("stage") in ("INTERESTED", "INQUIRY"):
                    silent_interested += 1

        # الاعتراضات بالنوع (متسجلة لكل منتج وقت حدوثها)
        for key, types in (s.get("objections_by_product") or {}).items():
            rec = _pi(key)
            rec["expensive"] += types.get("expensive", 0)
            rec["unsure"]    += types.get("unsure", 0)
            rec["later"]     += types.get("later", 0)

    product_insights_list = sorted(
        product_insights.values(), key=lambda r: r["asked"], reverse=True)
    for r in product_insights_list:
        r["conversion"] = (r["orders"] / r["asked"] * 100) if r["asked"] else 0

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
        "product_insights": product_insights_list,   # ⭐ رؤى المنتجات
        "silence": {   # ⭐ فين العملاء بيسكتوا
            "after_price": silent_after_price,
            "after_obj": silent_after_obj,
            "first_msg": silent_first_msg,
            "interested": silent_interested,
            "price_reply_rate": round(replied_after_price / price_quoted_total * 100, 0) if price_quoted_total else 0,
        },
        "updated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "_filter": {
            "from": date_from, "to": date_to,
            "active": bool(date_from or date_to),
            "n_states": len(states),
        },
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

    # ── HTML رؤى المنتجات ──
    pins = data.get("product_insights", [])
    if pins:
        def _chip(n, color, label):
            if not n:
                return f'<span style="color:#CBD5E1;font-size:12px">{label}: 0</span>'
            return (f'<span style="background:{color}18;color:{color};border:1px solid {color}40;'
                    f'border-radius:8px;padding:2px 8px;font-size:12px;font-weight:700">'
                    f'{label}: {n}</span>')

        pi_rows = ""
        for r in pins[:25]:
            conv_color = "#10b981" if r["conversion"] >= 20 else ("#f97316" if r["conversion"] >= 8 else "#ef4444")
            total_obj = r["expensive"] + r["unsure"] + r["later"]
            # نسبة كل اعتراض من إجمالي اللي سألوا
            def pct(n):
                return f" ({n/r['asked']*100:.0f}%)" if r["asked"] and n else ""
            pi_rows += f"""
<div style="border:1px solid #E2E8F0;border-radius:12px;padding:14px;margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
    <strong style="font-size:14px">{r['name'][:45]}</strong>
    <span style="font-size:13px;color:#475569">
      سألوا عنه <b>{r['asked']}</b> · طلبوا <b style="color:#10b981">{r['orders']}</b> ·
      تحويل <b style="color:{conv_color}">{r['conversion']:.0f}%</b>
    </span>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
    {_chip(r['expensive'], '#ef4444', '💰 غالي' + pct(r['expensive']))}
    {_chip(r['unsure'], '#f97316', '🤔 مش متأكد' + pct(r['unsure']))}
    {_chip(r['later'], '#eab308', '⏳ بعدين' + pct(r['later']))}
    {_chip(r['interested_now'], '#3b82f6', '❤️ مهتمين حالياً')}
    {_chip(r['price_silent'], '#f43f5e', '💸 شافوا السعر وسكتوا')}
    {_chip(r['objecting_now'], '#8b5cf6', '⚠️ معترضين حالياً')}
  </div>
</div>"""

        product_insights_html = f"""
<div class="sec" style="border:2px solid #0ea5e9">
  <h3>🔬 رؤى المنتجات — أداء واعتراضات كل منتج</h3>
  <p style="font-size:12px;color:#94a3b8;margin:0 0 12px">
    بدل ما تقرا مئات المحادثات: كل منتج وكام عميل اهتم بيه، اعترض على إيه بالظبط، وكام طلب فعلاً.
    التحويل الأخضر ≥20% ممتاز · البرتقالي 8-20% متوسط · الأحمر أقل من 8% محتاج مراجعة.
  </p>
  {pi_rows}
</div>"""
    else:
        product_insights_html = """
<div class="sec" style="border:2px solid #0ea5e9">
  <h3>🔬 رؤى المنتجات — أداء واعتراضات كل منتج</h3>
  <p style="font-size:13px;color:#64748b;margin:0">
    لسه مفيش بيانات — القسم بيتملأ تلقائياً مع أول محادثات العملاء.
    هتشوف هنا لكل منتج: كام عميل سأل عنه، كام اعترض (غالي/مش متأكد/بعدين)، وكام طلب فعلاً — بنسب واضحة.
  </p>
</div>"""

    # ── HTML فين العملاء بيسكتوا ──
    sil = data.get("silence", {})
    silence_html = f"""
<div class="sec" style="border:2px solid #f43f5e">
  <h3>🔇 فين العملاء بيسكتوا؟ — نقاط موت المحادثات</h3>
  <p style="font-size:12px;color:#94a3b8;margin:0 0 12px">
    محادثات صامتة 6+ ساعات بدون طلب — مقسّمة حسب آخر محطة قبل الصمت.
    نسبة الرد بعد سماع السعر مؤشر مباشر على قوة عرض السعر.
  </p>
  <div class="grid">
    <div class="card" style="border:2px solid #f43f5e"><h2>💸 شافوا السعر وسكتوا</h2><div class="num" style="color:#f43f5e">{sil.get('after_price', 0)}</div></div>
    <div class="card"><h2>⚠️ اعترضوا وسكتوا</h2><div class="num" style="color:#8b5cf6">{sil.get('after_obj', 0)}</div></div>
    <div class="card"><h2>👋 سألوا سؤال وسكتوا</h2><div class="num" style="color:#eab308">{sil.get('first_msg', 0)}</div></div>
    <div class="card"><h2>❤️ كانوا مهتمين وسكتوا</h2><div class="num" style="color:#3b82f6">{sil.get('interested', 0)}</div></div>
    <div class="card" style="border:2px solid #10b981"><h2>📈 نسبة الرد بعد سماع السعر</h2><div class="num green">{sil.get('price_reply_rate', 0):.0f}%</div></div>
  </div>
</div>"""

    # ── شريط فلتر التاريخ ──
    from datetime import datetime as _dt
    _flt = data.get("_filter", {})
    _df, _dt_ts = _flt.get("from"), _flt.get("to")
    _range_key = data.get("_range_key", "all")
    _df_val = _dt.fromtimestamp(_df).strftime("%Y-%m-%d") if _df else ""
    _to_val = _dt.fromtimestamp(_dt_ts).strftime("%Y-%m-%d") if _dt_ts else ""
    _slug = tenant.slug
    _key = data.get("_akey", "")
    # لو الدخول بالجلسة (مش بـ key)، الروابط تشتغل من غير key
    _key_param = f"key={_key}&" if _key else ""
    def _bcls(k):
        return "background:#7c3aed;color:#fff" if _range_key == k else "background:#1e293b;color:#94a3b8"
    _scope_note = ""
    if _flt.get("active"):
        _rng = []
        if _df_val: _rng.append(f"من {_df_val}")
        if _to_val: _rng.append(f"إلى {_to_val}")
        _scope_note = (f'<div style="text-align:center;color:#a78bfa;font-size:13px;margin:8px 0">'
                       f'🔍 عرض <b>{_flt.get("n_states",0)}</b> محادثة نشطة {" ".join(_rng)} — '
                       f'كل الأرقام تحت بتعكس الفترة دي (الطلبات مؤرّخة بدقة)</div>')
    _base = f"/analytics/{_slug}?{_key_param}"
    filter_bar = f"""
<div style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:14px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:center">
    <span style="color:#e2e8f0;font-weight:700;font-size:14px">📅 الفترة:</span>
    <a href="{_base}range=today" style="{_bcls('today')};padding:6px 16px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:700">اليوم</a>
    <a href="{_base}range=7d" style="{_bcls('7d')};padding:6px 16px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:700">آخر 7 أيام</a>
    <a href="{_base}range=30d" style="{_bcls('30d')};padding:6px 16px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:700">آخر 30 يوم</a>
    <a href="{_base}range=all" style="{_bcls('all')};padding:6px 16px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:700">الكل</a>
    <span style="color:#475569;margin:0 4px">|</span>
    <form method="get" action="/analytics/{_slug}" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
      {'<input type="hidden" name="key" value="' + _key + '">' if _key else ''}
      <input type="date" name="from" value="{_df_val}" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:5px 8px;font-size:13px">
      <span style="color:#94a3b8">←</span>
      <input type="date" name="to" value="{_to_val}" style="background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:5px 8px;font-size:13px">
      <button type="submit" style="background:#10b981;color:#fff;border:none;border-radius:6px;padding:6px 16px;font-size:13px;font-weight:700;cursor:pointer">تطبيق</button>
    </form>
  </div>
  {_scope_note}
</div>"""

    return f"""<!DOCTYPE html><html dir="rtl" lang="ar">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{tenant.business_name} — Analytics</title><style>{_CSS}</style>
<script>setTimeout(()=>location.reload(), 60000)</script>
</head><body>
<h1>📊 لوحة تحليلات {tenant.business_name}</h1>
{filter_bar}

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
{silence_html}
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

    # ── تحديد الفترة ──
    import time as _time
    now = _time.time()
    range_key = request.args.get("range", "")
    from_str  = (request.args.get("from") or "").strip()
    to_str    = (request.args.get("to") or "").strip()
    date_from = date_to = None

    if from_str or to_str:
        range_key = "custom"
        from datetime import datetime as _dt
        try:
            if from_str:
                date_from = _dt.strptime(from_str, "%Y-%m-%d").timestamp()
            if to_str:
                date_to = _dt.strptime(to_str, "%Y-%m-%d").timestamp() + 86399
        except ValueError:
            pass
    elif range_key == "today":
        date_from = now - 86400
    elif range_key == "7d":
        date_from = now - 7 * 86400
    elif range_key == "30d":
        date_from = now - 30 * 86400
    else:
        range_key = "all"

    data = get_tenant_analytics(tenant, date_from=date_from, date_to=date_to)
    # نمرّر للـ builder: الـ key (لبناء روابط الأزرار) والـ range النشط
    data["_range_key"] = range_key
    data["_akey"] = request.args.get("key", "")
    return build_analytics_html(tenant, data), 200, {"Content-Type": "text/html; charset=utf-8"}
