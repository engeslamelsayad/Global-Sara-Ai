# -*- coding: utf-8 -*-
"""
questions.py — «أسئلة تليجرام»: البوت بيسأل الفريق اللي مايعرفوش، وبيفتكر الإجابة.

من مراجعة 158 محادثة حقيقية: المقاسات والألوان كانت أكتر حاجة اتسألت، ومش مكتوبة،
فالبوت قال «نراجعها مع الموظف ونرجعلك» 18 مرة — ومحدش رجع لحد.

الدورة:
  1. العميل سأل عن معلومة مش عند البوت → البوت يقوله «هتأكد وأرجعلك» ويكتب [ASK|السؤال]
     (bot_engine بيشيل العلامة من الرد — العميل مابيشوفهاش).
  2. السؤال بيتسجل في BotQuestion (pending) ويتبعت على تليجرام للشخص المربوط
     (tenant.questions_chat_id — بيتربط بكود ASK-XXXXXX من صفحة «أسئلة تليجرام»).
     نفس السؤال من عميل تاني = بيتضاف للمستنيين من غير تنبيه جديد.
  3. الشخص يعمل Reply على رسالة التنبيه بالإجابة → تتحفظ (answered) وتدخل البرومبت
     كمعلومة مؤكدة لكل العملاء الجايين، وتتبعت للعملاء اللي كانوا مستنيين (جوه 23 ساعة
     — شباك الـ 24 ساعة بتاع Meta).
  4. التاجر يراجع/يعدّل/يوقف أي إجابة من الداشبورد.

لو مفيش حد مربوط: البوت ممنوع يوعد إنه هيرجع (البرومبت بيقوله كده) — مفيش وعود فاضية.
"""

import json
import re
import secrets
import string
import time
from datetime import datetime

# ⚠️ عقد بين البرومبت والـ parser (القاعدة #4) — لو غيّرت الصيغة هنا غيّرها في prompt_section
ASK_PATTERN = re.compile(r"\[ASK\|([^\]\n]{3,300})\]")
LINK_PREFIX = "ASK-"
MAX_ASKERS = 20              # عملاء مستنيين على نفس السؤال
REPLY_WINDOW_HOURS = 23      # Meta بتسمح بالرد جوه 24 ساعة من آخر رسالة للعميل
MAX_FACTS_IN_PROMPT = 40
SIMILARITY = 0.5             # تشابه الكلمات عشان سؤالين يتعتبروا نفس السؤال


# ── أدوات ─────────────────────────────────────────────────────────────

def enabled(tenant):
    """فيه حد مربوط يجاوب؟ (من غيره البوت مايوعدش إنه هيرجع)"""
    import telegram_bot
    return bool(tenant and getattr(tenant, "questions_chat_id", None) and telegram_bot.is_configured())


def new_link_code():
    return LINK_PREFIX + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def extract(text):
    """(النص من غير علامات [ASK|…]، [الأسئلة]) — العلامة بتتشال دايماً حتى لو الميزة مقفولة."""
    if not text:
        return text, []
    qs = [q.strip() for q in ASK_PATTERN.findall(text) if q.strip()]
    clean = ASK_PATTERN.sub("", text).strip() if qs or "[ASK|" in text else text
    return clean, qs


def _tokens(s):
    from bot_engine import _normalize_ar
    t = re.sub(r"[^\w\s]", " ", _normalize_ar(s or ""))
    return {w for w in t.split() if len(w) > 2}


def _similar(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= SIMILARITY


def _esc(s):
    import telegram_bot
    return telegram_bot._esc(s)


# ── 1+2: السؤال من البوت → BotQuestion + تنبيه تليجرام ────────────────

def register(tenant, question, product=None, psid=None, page_id=None, platform=None):
    """بيرجّع BotQuestion (جديد أو موجود). مابيرفعش exceptions للـ caller."""
    from models import db, BotQuestion
    question = question.strip()[:300]
    pkey = product.product_key if product else None
    asker = {"psid": psid, "page_id": page_id, "platform": platform or "facebook",
             "t": time.time()} if psid else None

    # نفس السؤال اتجاوب قبل كده؟ يبقى المعلومة في البرومبت أصلاً — مانزعجش الفريق
    answered = BotQuestion.query.filter_by(tenant_id=tenant.id, status="answered").all()
    for q in answered:
        if (q.product_key or None) == pkey and _similar(q.question, question):
            return q

    pending = BotQuestion.query.filter_by(tenant_id=tenant.id, status="pending").all()
    for q in pending:
        if (q.product_key or None) == pkey and _similar(q.question, question):
            askers = json.loads(q.askers or "[]")
            if asker and not any(a.get("psid") == psid for a in askers):
                askers = (askers + [asker])[-MAX_ASKERS:]
                q.askers = json.dumps(askers)
                q.times_asked = (q.times_asked or 1) + 1
                db.session.commit()
            return q

    q = BotQuestion(tenant_id=tenant.id, question=question, product_key=pkey, status="pending",
                    source="telegram", askers=json.dumps([asker] if asker else []), times_asked=1)
    db.session.add(q)
    db.session.commit()
    _send_alert(tenant, q, product)
    return q


def _send_alert(tenant, q, product=None):
    import telegram_bot
    if not enabled(tenant):
        return
    lines = [f"❓ <b>سؤال من عميل — {_esc(tenant.business_name)}</b>"]
    if product:
        lines.append(f"المنتج: {_esc(product.name)}")
    lines.append(f"السؤال: <b>{_esc(q.question)}</b>")
    lines.append("\n↩️ اعمل <b>Reply</b> على الرسالة دي بالإجابة — البوت هيحفظها ويرد بيها "
                 "على العميل، وعلى أي حد يسأل نفس السؤال بعد كده.")
    mid = telegram_bot.send_message_get_id(tenant.questions_chat_id, "\n".join(lines))
    if mid:
        from models import db
        q.tg_chat_id, q.tg_message_id = str(tenant.questions_chat_id), str(mid)
        db.session.commit()
        print(f"❓ سؤال اتبعت لتليجرام ({tenant.slug}): {q.question[:60]}")


# ── 3: الإجابة → ذاكرة البوت + العملاء المستنيين ──────────────────────

def apply_answer(q, answer, source=None):
    """يحفظ الإجابة ويبعتها للعملاء اللي كانوا مستنيين. بيرجّع عدد العملاء اللي اتبعتلهم."""
    from models import db
    answer = (answer or "").strip()[:1000]
    if not answer:
        return 0
    was_pending = q.status == "pending"
    q.answer, q.status = answer, "answered"
    q.answered_at = datetime.utcnow()
    if source:
        q.source = source
    waiting = json.loads(q.askers or "[]") if was_pending else []
    q.askers = "[]"
    db.session.commit()
    return _notify_waiting(q, waiting) if waiting else 0


def _notify_waiting(q, waiting):
    import dialect_text
    from bot_engine import get_tenant_for_page, load_state, save_state, send_message
    sent, now = 0, time.time()
    for a in waiting:
        if now - (a.get("t") or 0) > REPLY_WINDOW_HOURS * 3600 or not a.get("page_id"):
            continue
        try:
            bundle = get_tenant_for_page(a["page_id"])
            if not bundle or bundle["tenant"].id != q.tenant_id:
                continue
            state = load_state(q.tenant_id, a["psid"]) or {}
            if state.get("has_order") or state.get("is_human_handoff"):
                continue
            bc = bundle.get("bot_config")
            msg = dialect_text.get_msg(bc.dialect if bc else "مصري", "qa_answer", a=q.answer)
            send_message(bundle, a["psid"], msg, a["page_id"], a.get("platform") or "facebook")
            hist = state.get("history") or []
            if hist and hist[-1].get("role") == "assistant":
                hist[-1]["content"] = f"{hist[-1]['content']}\n\n{msg}"
            else:
                hist.append({"role": "assistant", "content": msg})
            state["history"] = hist[-16:]
            save_state(q.tenant_id, a["psid"], state)
            sent += 1
        except Exception as e:
            print(f"⚠️ qa notify failed for {str(a.get('psid'))[:12]}: {e}")
    if sent:
        print(f"✅ إجابة «{q.question[:40]}» اتبعتت لـ {sent} عميل كانوا مستنيين")
    return sent


def handle_telegram_message(msg, app):
    """
    من process_link_messages: كود ربط ASK-XXXXXX، أو Reply على تنبيه سؤال.
    بيرجّع True لو الرسالة اتعالجت هنا.
    """
    import telegram_bot
    from models import db, Tenant, BotQuestion
    chat = msg.get("chat", {}) or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return False

    if text.upper().startswith(LINK_PREFIX):
        with app.app_context():
            t = Tenant.query.filter_by(questions_link_code=text.upper()).first()
            if not t:
                telegram_bot.send_message(chat_id, "❌ الكود ده مش صحيح أو انتهت صلاحيته. خد كود جديد من صفحة «أسئلة تليجرام».")
                return True
            who = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                or (("@" + chat["username"]) if chat.get("username") else "") or chat_id
            t.questions_chat_id, t.questions_contact, t.questions_link_code = chat_id, who[:120], None
            db.session.commit()
            telegram_bot.send_message(chat_id,
                f"✅ اتربطت بأسئلة عملاء <b>{_esc(t.business_name)}</b>.\n\n"
                f"لما عميل يسأل عن معلومة البوت مايعرفهاش، هتوصلك هنا — اعمل Reply بالإجابة.")
            print(f"🔗 أسئلة تليجرام: {t.slug} → {who}")
        return True

    reply_to = msg.get("reply_to_message") or {}
    if not reply_to.get("message_id"):
        return False
    with app.app_context():
        q = BotQuestion.query.filter_by(tg_chat_id=chat_id,
                                        tg_message_id=str(reply_to["message_id"])).first()
        if not q:
            return False
        n = apply_answer(q, text, source="telegram")
        extra = f" واتبعتت لـ {n} عميل كانوا مستنيين" if n else ""
        telegram_bot.send_message(chat_id, f"✅ اتحفظت في ذاكرة البوت{extra}. تقدر تعدّلها من صفحة «أسئلة تليجرام».")
    return True


# ── البرومبت ──────────────────────────────────────────────────────────

def prompt_section(tenant, products):
    """قسم المعلومات المؤكدة + إزاي البوت يتصرف لو معلومة مش عنده."""
    from models import BotQuestion
    facts = (BotQuestion.query.filter_by(tenant_id=tenant.id, status="answered")
             .order_by(BotQuestion.answered_at.desc()).limit(MAX_FACTS_IN_PROMPT).all())
    names = {p.product_key: p.name for p in products}
    out = []
    if facts:
        out.append("[📚 معلومات أكدها فريق المتجر — صحيحة، استخدميها زي بيانات المنتج]")
        for f in facts:
            where = f" ({names[f.product_key]})" if f.product_key in names else ""
            out.append(f"- س{where}: {f.question}\n  ج: {f.answer}")
    if enabled(tenant):
        out.append(
            "[❓ لو العميل سأل عن معلومة مش عندك]\n"
            "(مقاس، لون، خامة، توفر، أو أي تفصيلة مش في بيانات المنتج ولا في المعلومات المؤكدة فوق):\n"
            "- قوليله إنك هتتأكدي من فريق المتجر وترجعيله هنا في نفس المحادثة، وكمّلي بالمعلومة "
            "المتاحة (زي المعاينة قبل الدفع).\n"
            "- واكتبي في آخر ردك علامة واحدة بالشكل ده بالظبط: [ASK|السؤال بصيغة واضحة ومختصرة]\n"
            "- العلامة للنظام بس — العميل مش بيشوفها. ماتكتبيهاش لمعلومة موجودة عندك.")
    else:
        out.append(
            "[❓ لو العميل سأل عن معلومة مش عندك]\n"
            "قوليها بوضوح إنها مش متوفرة عندك دلوقتي، وكمّلي بالمعلومة المتاحة (زي المعاينة قبل "
            "الدفع). ❌ ماتوعديش إنك هترجعيله ولا إنك هتسألي حد أو هتراجعي مع موظف.")
    return "\n".join(out) + "\n"
