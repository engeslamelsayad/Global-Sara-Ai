# -*- coding: utf-8 -*-
"""
ads.py — ربط إعلانات Click-to-Message بالمنتجات (جدول AdProduct)

المشكلة: عناوين الإعلانات زي «New Sales Ad_Group_1» مابتطابقش أي منتج، فعميل جاي
من إعلان الموكيت بيكتب «السعر» والبوت يسأله «أي منتج؟» — 67 محادثة في يوم واحد
بدأت كده و25 منهم مشيوا على طول.

الحل:
  1. التاجر يربط الإعلان بالمنتج من الداشبورد (manual) — أعلى أولوية.
  2. ربط تلقائي (auto): كل عميل جاي من الإعلان وبان المنتج اللي عايزه (سمّاه، أو بعت
     صورة الإعلان والبوت عرفه) = صوت للمنتج ده. منتج واحد ياخد AUTO_MIN_VOTES عملاء
     مختلفين → الإعلان يتربط بيه.
  3. أمان: لو منتج تاني جاب CONFLICT_MIN_VOTES عملاء من نفس الإعلان → الربط التلقائي
     يقف (conflict) لحد ما التاجر يختار. وارد حملتين يدخلوا على بعض وقت الإطلاق،
     وماينفعش عميل يسأل عن منتج ياخد سعر منتج تاني.
"""

import json
from datetime import datetime

AUTO_MIN_VOTES = 2       # عملاء مختلفين على نفس المنتج عشان الربط التلقائي يشتغل
CONFLICT_MIN_VOTES = 2   # عملاء على منتج تاني من نفس الإعلان = تعارض → وقف الربط
MAX_VOTERS = 300         # آخر sender_ids اللي صوّتوا (منع تكرار صوت نفس العميل)


def ad_key_from_referral(referral):
    """(ad_key, ad_title) من الـ referral. الـ ad_id أدق من العنوان (إعلانات كتير
    بتشترك في نفس العنوان الافتراضي)."""
    ad_id = str(referral.get("ad_id") or "").strip()
    title = ((referral.get("ads_context_data") or {}).get("ad_title") or "").strip()
    if ad_id:
        return ad_id[:120], title
    if title:
        return ("title:" + title)[:120], title
    return None, title


def _row(tenant_id, ad_key):
    from models import AdProduct
    return AdProduct.query.filter_by(tenant_id=tenant_id, ad_key=ad_key).first()


def resolved_product_key(row):
    """المنتج اللي الإعلان مربوط بيه فعلاً (manual أو auto) — conflict = مفيش."""
    if not row or not row.product_key:
        return None
    return row.product_key if row.mode in ("manual", "auto") else None


def record_hit(tenant_id, ad_key, ad_title, sender_id):
    """يسجّل إن عميل جه من الإعلان، ويرجّع product_key المربوط (أو None).
    عميل واحد بيتعدّ مرة واحدة حتى لو Meta بعتت الـ referral مرتين."""
    from models import db, AdProduct
    row = _row(tenant_id, ad_key)
    if not row:
        row = AdProduct(tenant_id=tenant_id, ad_key=ad_key, ad_title=(ad_title or "")[:300],
                        mode="auto", votes="{}", voters="[]", hits=0)
        db.session.add(row)
    if ad_title:
        row.ad_title = ad_title[:300]
    seen = json.loads(row.voters or "[]")
    if sender_id not in seen:
        row.hits = (row.hits or 0) + 1
    row.last_seen = datetime.utcnow()
    db.session.commit()
    return resolved_product_key(row)


def vote(tenant_id, ad_key, product_key, sender_id):
    """
    عميل من الإعلان بان إنه عايز product_key. بيرجّع (mode, product_key) بعد التحديث.
    صوت واحد لكل عميل. manual مابيتأثرش. conflict بيفضل conflict لحد ما التاجر يختار.
    """
    from models import db
    row = _row(tenant_id, ad_key)
    if not row or not product_key:
        return None, None
    voters = json.loads(row.voters or "[]")
    if sender_id in voters:
        return row.mode, row.product_key
    voters = (voters + [sender_id])[-MAX_VOTERS:]
    votes = json.loads(row.votes or "{}")
    votes[product_key] = votes.get(product_key, 0) + 1
    row.voters, row.votes = json.dumps(voters), json.dumps(votes, ensure_ascii=False)

    became_conflict = False
    if row.mode == "auto":
        strong = [k for k, v in votes.items() if v >= AUTO_MIN_VOTES]
        others = [k for k, v in votes.items() if v >= CONFLICT_MIN_VOTES]
        if len(others) >= 2:
            row.mode, row.product_key = "conflict", None
            became_conflict = True
        elif len(strong) == 1:
            row.product_key = strong[0]
    db.session.commit()

    if became_conflict:
        print(f"⚠️ إعلان «{(row.ad_title or row.ad_key)[:40]}» جاب عملاء لمنتجين "
              f"مختلفين {votes} — الربط التلقائي اتوقف لحد ما التاجر يختار")
        _notify_conflict(tenant_id, row, votes)
    elif row.mode == "auto" and row.product_key == product_key:
        print(f"📢 إعلان «{(row.ad_title or row.ad_key)[:40]}» → {product_key} "
              f"(تلقائي، {votes.get(product_key)} عملاء)")
    return row.mode, row.product_key


def _notify_conflict(tenant_id, row, votes):
    """تنبيه تليجرام للتاجر (لو مربوط) — مرة واحدة لما التعارض يحصل."""
    try:
        import telegram_bot
        from models import Tenant
        t = Tenant.query.get(tenant_id)
        if not (t and t.telegram_enabled and t.telegram_chat_id and telegram_bot.is_configured()):
            return
        names = {p.product_key: p.name for p in t.products}
        lines = "\n".join(f"• {telegram_bot._esc(names.get(k, k))}: {v} عملاء"
                          for k, v in votes.items())
        telegram_bot.send_message(
            t.telegram_chat_id,
            f"⚠️ <b>إعلان جايب عملاء لأكتر من منتج</b>\n"
            f"الإعلان: {telegram_bot._esc(row.ad_title or row.ad_key)}\n{lines}\n\n"
            f"وقفت الربط التلقائي عشان محدش ياخد سعر منتج غلط. "
            f"اختار المنتج الصح من صفحة «الإعلانات» في لوحة التحكم.")
    except Exception as e:
        print(f"⚠️ ad conflict notify failed: {e}")
