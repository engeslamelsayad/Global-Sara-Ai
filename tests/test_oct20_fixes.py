# -*- coding: utf-8 -*-
"""
إصلاحات من مراجعة logs 20 أكتوبر (154 محادثة، 0 أوردر — 93 من 130 مشيوا بعد أول سعر):
حارس الرد (سعر/عملة/«سجلت طلبك»)، سؤال المدينة بعد السعر، فلتر أسئلة تليجرام،
والمتابعات جوه شباك Meta (23 ساعة).
"""
import os
import sys
import time
import types
from decimal import Decimal

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app_main
import bot_engine
import questions
import recovery
import provisioning
import sales_playbook as sp
from models import db, BotConfig, FollowupStage, Product

P = types.SimpleNamespace(name="طقم موكيت", price_note="199 بدل 280", price_amount=None,
                          shipping_fee=None, product_key="rug")
CAPS = types.SimpleNamespace(name="كبسولات", price_note="", price_amount=Decimal("159"),
                             shipping_fee=None, product_key="caps")


# ── حارس الرد ──

@pytest.mark.parametrize("txt", ["السعر ٢٠٥٠ دينار للطقم", "السعر 2,050 دينار", "بـ 350 دينار بس"])
def test_guard_flags_made_up_price(txt):
    assert [p[0] for p in bot_engine.reply_problems(txt, [P, CAPS], "دينار")] == ["price"]


@pytest.mark.parametrize("txt", ["الطقم بـ199 دينار بدل 280", "عبوتين بـ318 دينار",
                                 "يعني حوالي 5.3 دينار في اليوم", "بعد خصم 10% يبقى 179 دينار",
                                 "3 أطقم بـ597 دينار", "باش يتسجّل الطلب نحتاج اسمك"])
def test_guard_allows_real_prices(txt):
    assert bot_engine.reply_problems(txt, [P, CAPS], "دينار") == []


def test_guard_wrong_currency_and_fake_order():
    kinds = [p[0] for p in bot_engine.reply_problems("السعر 25 دولار", [P], "دينار")]
    assert kinds == ["currency"]
    assert [p[0] for p in bot_engine.reply_problems("تمام سجلت طلبك 👍", [P], "دينار")] == ["fake_order"]
    # مع ORDER أو طلب متسجل فعلاً = سليم
    assert bot_engine.reply_problems("سجلت طلبك [ORDER|rug|1|أ|0912345678|طرابلس]", [P], "دينار") == []
    assert bot_engine.reply_problems("سجلت طلبك", [P], "دينار", {"has_order": True}) == []


def test_drop_bad_sentences_keeps_rest():
    probs = bot_engine.reply_problems("أهلين! السعر ٢٠٥٠ دينار. تبي نجهزلك؟", [P], "دينار")
    assert bot_engine._drop_bad_sentences("أهلين! السعر ٢٠٥٠ دينار. تبي نجهزلك؟", probs) == "أهلين! تبي نجهزلك؟"


@pytest.fixture(scope="module")
def tenant():
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("oct20", "آمان", "oct20@x.com", "pass12345", dialect="ليبي")
        t.currency = "دينار"
        db.session.add(Product(tenant_id=t.id, product_key="rug", name="طقم موكيت",
                               keywords="موكيت", price_note="199 بدل 280", is_active=True))
        bc = BotConfig.query.filter_by(tenant_id=t.id).first()
        bc.sales_style = "assertive"
        db.session.commit()
        yield app, t.id


def _client(replies, captured):
    it = iter(replies)

    def create(**kw):
        captured.append(kw)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=next(it))],
                                     stop_reason="end_turn")
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def _system_text(kw):
    return "\n".join(b["text"] if isinstance(b, dict) else str(b) for b in kw["system"])


def _bundle(tid):
    from models import Tenant
    return bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))


def test_guard_retries_with_correction(tenant, monkeypatch):
    app, tid = tenant
    cap = []
    monkeypatch.setattr(bot_engine, "client", _client(["السعر 2050 دينار", "السعر 199 دينار بدل 280"], cap))
    with app.app_context():
        st = bot_engine.default_state(tid, "P", "facebook")
        out = bot_engine.get_ai_response(_bundle(tid), "g1", "بكم الموكيت", st)
    assert len(cap) == 2 and "تصحيح إجباري" in _system_text(cap[1])
    assert "199" in str(out) and "2050" not in str(out)


def test_guard_drops_sentence_when_retry_still_wrong(tenant, monkeypatch):
    app, tid = tenant
    cap = []
    monkeypatch.setattr(bot_engine, "client",
                        _client(["أهلين. السعر 2050 دينار.", "أهلين. السعر 2050 دينار."], cap))
    with app.app_context():
        st = bot_engine.default_state(tid, "P", "facebook")
        out = bot_engine.get_ai_response(_bundle(tid), "g2", "بكم الموكيت", st)
    assert "2050" not in str(out) and "أهلين" in str(out)


# ── سؤال المدينة بعد السعر ──

def test_first_price_hint_asks_city(tenant, monkeypatch):
    app, tid = tenant
    cap = []
    monkeypatch.setattr(bot_engine, "client", _client(["أهلين"], cap))
    with app.app_context():
        st = bot_engine.default_state(tid, "P", "facebook")
        bot_engine.get_ai_response(_bundle(tid), "c1", "بكم الموكيت", st)
    assert "المدينة بس" in _system_text(cap[-1])
    assert sp.first_price_hint("calm") is None
    assert "المدينة بس" in sp.price_hint("assertive") and "المدينة بس" in sp.price_hint("balanced")


# ── أسئلة تليجرام: معلومة متجر بس ──

@pytest.mark.parametrize("q,ok", [
    ("ما هي الألوان المتوفرة لطقم الموكيت البوهيمي للمطبخ؟", True),
    ("كم غرام وزن عبوة كريم العناية بتجاعيد الرقبة؟", True),
    ("ما هي المقاسات اللي تبيها للمطبخ (الطول والعرض)؟", False),
    ("هل تبي الطقم باللون الأساسي؟", False),
    ("ما هو الطول والعرض المطلوب للموكيت؟ وكم قياس المطبخ تقريباً؟", False),
])
def test_store_question_filter(q, ok):
    assert questions.is_store_question(q) is ok


# ── المتابعات جوه شباك Meta ──

def test_followups_respect_23h_window(tenant, monkeypatch):
    app, tid = tenant
    now = time.time()
    with app.app_context():
        FollowupStage.query.filter_by(tenant_id=tid).delete()
        db.session.add_all([
            FollowupStage(tenant_id=tid, stage_number=1, hours_after_last=24, discount_percent=10,
                          message_text="", is_active=True),     # برا الشباك — مايوقفش اللي بعده
            FollowupStage(tenant_id=tid, stage_number=2, hours_after_last=3, discount_percent=0,
                          message_text="", is_active=True),
        ])
        db.session.commit()

    def st(hours_ago):
        return {"page_id": "P", "platform": "facebook", "history": [{"role": "user", "content": "x"}],
                "last_message": now - hours_ago * 3600}
    states = [("fresh", st(4)), ("old", st(30))]
    sent = []
    monkeypatch.setattr(bot_engine, "list_tenant_states_with_ids",
                        lambda t: states if t == tid else [])
    monkeypatch.setattr(bot_engine, "get_tenant_for_page",
                        lambda p: {"products": [], "bot_config": None})
    monkeypatch.setattr(bot_engine, "send_message", lambda b, sid, msg, *a: sent.append(sid))
    monkeypatch.setattr(bot_engine, "save_state", lambda *a: None)
    monkeypatch.setattr(recovery, "is_quiet_hours", lambda d, now=None: False)
    recovery.run_followups(app)
    assert sent == ["fresh"]
    assert states[0][1]["followup_stages_sent"] == [2]


def test_default_stages_fit_window():
    import default_keywords
    assert sum(h for _, h, _ in default_keywords.DEFAULT_FOLLOWUP_STAGES) < recovery.WINDOW_HOURS


# ── 21 أكتوبر: سؤال آه/لا للإغلاق + وعد «الموظف يتابع» + ساعات الهدوء ──

A = types.SimpleNamespace(sales_style="assertive", offer_hesitation_percent=None)
CREAM = types.SimpleNamespace(name="كريم", price_note="159 بدلا من 195", price_amount=None,
                              shipping_fee=None)


def _kinds(txt, style=A, user="", state=None):
    return [p[0] for p in bot_engine.reply_problems(txt, [CREAM], "دينار", state or {}, style, user)]


def test_yesno_close_flagged():
    assert _kinds("الكريم بـ 159 دينار بدل 195. تبي نجهزلك عبوة؟") == ["yesno_close"]
    assert _kinds("تحب تطلبه؟") == ["yesno_close"]


@pytest.mark.parametrize("txt", ["السعر 159 دينار. إنت من أي مدينة؟", "تبي نجهزلك عبوة؟ وإنت من وين؟",
                                 "تحب تطلبه بالبيج ولا الرمادي؟",
                                 "إذا تبي تكمل الطلب، ابعثلي الاسم ورقم الهاتف"])
def test_yesno_close_allowed(txt):
    assert _kinds(txt) == []


def test_yesno_close_not_for_calm_or_after_order():
    assert _kinds("تبي نجهزلك؟", style=types.SimpleNamespace(sales_style="calm")) == []
    assert _kinds("تبي نجهزلك؟", state={"has_order": True}) == []


def test_fake_employee():
    assert _kinds("ما عنديش صورة، الموظف يتابع معاك هنا.") == ["fake_employee"]
    assert _kinds("حاضر، الموظف هيتابع معاك", user="ابي نكلم موظف") == []
    assert _kinds("هتأكد من الفريق وأرجعلك [ASK|ألوان الطقم المتوفرة؟]") == []


def test_yesno_kept_employee_dropped_when_retry_fails():
    txt = "السعر 159 دينار. تبي نجهزلك عبوة؟ الموظف يتابع معاك."
    probs = bot_engine.reply_problems(txt, [CREAM], "دينار", {}, A)
    assert bot_engine._drop_bad_sentences(txt, probs) == "السعر 159 دينار. تبي نجهزلك عبوة؟"


def test_quiet_hours_by_dialect():
    midnight_utc = 1792540800 - 1792540800 % 86400          # 00:00 UTC
    assert recovery.is_quiet_hours("ليبي", midnight_utc + 0 * 3600)       # 2 الصبح ليبيا
    assert not recovery.is_quiet_hours("ليبي", midnight_utc + 8 * 3600)   # 10 الصبح ليبيا
    assert recovery.is_quiet_hours("خليجي", midnight_utc + 5 * 3600)      # 8 الصبح السعودية
    assert not recovery.is_quiet_hours("خليجي", midnight_utc + 6 * 3600)


def test_followups_wait_in_quiet_hours(tenant, monkeypatch):
    app, tid = tenant
    sent = []
    with app.app_context():
        FollowupStage.query.filter_by(tenant_id=tid).delete()
        db.session.add(FollowupStage(tenant_id=tid, stage_number=1, hours_after_last=3,
                                     discount_percent=0, message_text="", is_active=True))
        db.session.commit()
    monkeypatch.setattr(bot_engine, "list_tenant_states_with_ids",
                        lambda t: [("q", {"page_id": "P", "history": [1],
                                          "last_message": time.time() - 5 * 3600})]
                        if t == tid else [])
    monkeypatch.setattr(bot_engine, "get_tenant_for_page",
                        lambda p: {"products": [], "bot_config": None})
    monkeypatch.setattr(bot_engine, "send_message", lambda b, sid, *a: sent.append(sid))
    monkeypatch.setattr(bot_engine, "save_state", lambda *a: None)
    monkeypatch.setattr(recovery, "is_quiet_hours", lambda d, now=None: True)
    recovery.run_followups(app)
    assert sent == []
    monkeypatch.setattr(recovery, "is_quiet_hours", lambda d, now=None: False)
    recovery.run_followups(app)
    assert sent == ["q"]
