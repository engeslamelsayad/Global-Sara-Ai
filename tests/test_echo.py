"""
tests/test_echo.py — سيناريوهات القاعدة #6 (echo / self-pause) من أول الـ webhook لآخره.

التشغيل:
    pip install -r requirements.txt pytest
    DISABLE_SCHEDULER=1 ANTHROPIC_API_KEY=x python -m pytest tests/ -q

أي تعديل في handle_echo أو note_customer_message لازم الملف ده يعدّي.
"""
import os
import sys

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")      # DB في الذاكرة — مايلمسش local_dev.db
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app_main
import bot_engine
import provisioning
import webhook
from models import db, Page, BotAppId

BOT, AUTO, HUMAN = "1399814178950481", "517173525759142", "777000111"
INBOX = bot_engine.META_INBOX_APP_ID
T = 1_800_000_000_000


@pytest.fixture(scope="module")
def env():
    app = app_main.app
    webhook.buffer_message = lambda *a, **k: None       # مانشغّلش الـ AI
    with app.app_context():
        t, _, _ = provisioning.create_tenant("echo-test", "Echo", "echo@test.com", "pass12345")
        db.session.add(Page(tenant_id=t.id, page_id="P1", platform="facebook",
                            access_token="tok", is_active=True))
        db.session.commit()
        tid = t.id
    return app, app.test_client(), tid


def _post(client, ev):
    client.post("/webhook", json={"object": "page", "entry": [{"id": "P1", "messaging": [ev]}]})


def customer(client, psid, ts, text="مرحبا"):
    _post(client, {"sender": {"id": psid}, "recipient": {"id": "P1"}, "timestamp": ts,
                   "message": {"mid": "m", "text": text}})


def echo(client, psid, app_id, ts, text="رد"):
    _post(client, {"sender": {"id": "P1"}, "recipient": {"id": psid}, "timestamp": ts,
                   "message": {"mid": "e", "is_echo": True, "app_id": int(app_id), "text": text}})


def paused(tid, psid):
    st = bot_engine.load_state(tid, psid)
    return bool(st and st.get("is_human_handoff"))


def with_history(tid, psid, bot_reply="أهلين! كيف بقدر ساعدك؟"):
    st = bot_engine.default_state(tid, "P1", "facebook")
    st["history"] = [{"role": "user", "content": "مرحبا"}, {"role": "assistant", "content": bot_reply}]
    bot_engine.save_state(tid, psid, st)


def trusted(app, tid, app_id):
    bot_engine.invalidate_tenant_cache()
    with app.app_context():
        return BotAppId.query.filter_by(tenant_id=tid, app_id=app_id).first() is not None


# ── السيناريوهات الأصلية ─────────────────────────────────────────────
def test_bot_own_echo_is_ignored_and_registered(env):
    app, c, tid = env
    with_history(tid, "U1")
    echo(c, "U1", BOT, T, text="أهلين! كيف بقدر ساعدك؟")
    assert not paused(tid, "U1") and trusted(app, tid, BOT)


def test_outreach_to_strangers_never_pauses_and_auto_trusts(env):
    app, c, tid = env
    for i, u in enumerate(["S1", "S2", "S3"]):
        echo(c, u, "888", T + i, text="عرض خاص!")
    assert not any(paused(tid, u) for u in ["S1", "S2", "S3"])
    assert trusted(app, tid, "888")


def test_human_moderator_in_conversation_pauses(env):
    _, c, tid = env
    with_history(tid, "U2")
    customer(c, "U2", T)
    echo(c, "U2", HUMAN, T + 20_000)
    assert paused(tid, "U2")


# ── الرد الآلي الفوري (من logs production أكتوبر 2026) ─────────────────
def test_fast_auto_reply_does_not_pause_then_trusted_after_3(env):
    app, c, tid = env
    for n, u in enumerate(["C1", "C2", "C3"], start=1):
        with_history(tid, u)
        customer(c, u, T)
        echo(c, u, AUTO, T + 1000)
        assert not paused(tid, u)
        assert trusted(app, tid, AUTO) == (n == 3)
    with_history(tid, "C4")
    customer(c, "C4", T)
    echo(c, "C4", AUTO, T + 60_000)          # موثّقة → مابتوقفش حتى متأخرة
    assert not paused(tid, "C4")


def test_meta_inbox_fast_is_automation_but_never_trusted(env):
    app, c, tid = env
    with_history(tid, "I1")
    customer(c, "I1", T)
    echo(c, "I1", INBOX, T + 500)
    assert not paused(tid, "I1") and not trusted(app, tid, INBOX)
    with_history(tid, "I2")
    customer(c, "I2", T)
    echo(c, "I2", INBOX, T + 15_000)          # إنسان بيرد من الإنبوكس
    assert paused(tid, "I2")


@pytest.mark.parametrize("delay_ms,expect_pause", [(3000, False), (3500, True)])
def test_window_boundary(env, delay_ms, expect_pause):
    _, c, tid = env
    psid = f"B{delay_ms}"
    with_history(tid, psid)
    customer(c, psid, T)
    echo(c, psid, "999000" + str(delay_ms), T + delay_ms)
    assert paused(tid, psid) == expect_pause


def test_no_customer_timestamp_falls_back_to_pause(env):
    _, c, tid = env
    with_history(tid, "B3")
    echo(c, "B3", "555000", T)                # مفيش رسالة عميل متسجّلة (بعد restart مثلاً)
    assert paused(tid, "B3")
