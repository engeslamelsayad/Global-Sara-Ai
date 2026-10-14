# -*- coding: utf-8 -*-
"""كشف الرفض الصريح + إن البرومبت بيشوفه في **نفس** الرسالة (مش الرسالة اللي بعدها)."""
import os
import sys

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import types

import pytest

import app_main
import bot_engine
import provisioning
from models import db, BotConfig


@pytest.mark.parametrize("msg", ["لا", "لأ", "لاااا", "لا.", "لا شكرا", "ما نبيش", "ما بدي",
                                 "مش عايز", "لا مش حابب", "No", "لا 🙏"])
def test_refusals(msg):
    assert bot_engine.is_refusal(msg)


@pytest.mark.parametrize("msg", ["لا بس عايز اعرف المقاس", "تم", "اه", "لازم يوصل بكرة",
                                 "لا يوجد عندي حساسية من المكونات دي", "", None])
def test_not_refusals(msg):
    assert not bot_engine.is_refusal(msg)


@pytest.fixture(scope="module")
def bundle():
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("refusal-test", "R", "r@test.com", "pass12345")
        db.session.commit()
        bc = BotConfig.query.filter_by(tenant_id=t.id).first()
        bc.sales_style = "assertive"
        db.session.commit()
        yield app, None, t.id


def _fake_client(captured):
    def create(**kw):
        captured.append(kw)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="تمام")], stop_reason="end_turn")
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def _system_text(kw):
    return "\n".join(b["text"] if isinstance(b, dict) else str(b) for b in kw["system"])


def test_refusal_hint_reaches_prompt_same_turn(bundle, monkeypatch):
    app, _, tid = bundle
    captured = []
    monkeypatch.setattr(bot_engine, "client", _fake_client(captured))
    with app.app_context():
        from models import Tenant
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        st = bot_engine.default_state(tid, "P", "facebook")
        st.update(price_quoted=True, stage="INTERESTED", messages_count=4)

        bot_engine.get_ai_response(b, "u1", "لا", st)
        assert st["stage"] == "OBJECTION" and st["refusals_count"] == 1
        assert "لسه قايل «لا» لأول مرة" in _system_text(captured[-1])

        bot_engine.get_ai_response(b, "u1", "لا شكرا", st)
        assert st["refusals_count"] == 2
        assert "وقفي المحاولة" in _system_text(captured[-1])
