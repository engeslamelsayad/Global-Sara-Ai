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
    recovery.run_followups(app)
    assert sent == ["fresh"]
    assert states[0][1]["followup_stages_sent"] == [2]


def test_default_stages_fit_window():
    import default_keywords
    assert sum(h for _, h, _ in default_keywords.DEFAULT_FOLLOWUP_STAGES) < recovery.WINDOW_HOURS
