# -*- coding: utf-8 -*-
"""
«أسئلة تليجرام» (questions.py): البوت بيسأل الفريق المعلومة اللي مايعرفهاش، الرد بيتحفظ في
ذاكرته ويتبعت للعميل اللي كان مستني. (من مراجعة حقيقية: «نراجع مع الموظف ونرجعلك» 18 مرة
ومحدش رجع.) تليجرام و Meta متزيّفين.
"""
import json
import os
import sys
import time
import types

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app_main
import bot_engine
import provisioning
import questions
import telegram_bot
from models import db, BotQuestion, Page, Product, Tenant

B = types.SimpleNamespace


@pytest.fixture(scope="module")
def env():
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("qa-a", "آمان", "qa-a@x.com", "pass12345", dialect="ليبي")
        db.session.add(Product(tenant_id=t.id, product_key="rug", name="طقم موكيت بوهيمي",
                               keywords="موكيت,سجاد المطبخ", price_note="199 بدل 280", is_active=True))
        db.session.add(Page(tenant_id=t.id, page_id="PQ", platform="facebook", access_token="t", is_active=True))
        o, _, _ = provisioning.create_tenant("qa-b", "B", "qa-b@x.com", "pass12345")
        db.session.commit()
        tid, oid = t.id, o.id
    c = app.test_client()
    c.post("/login", data={"email": "qa-a@x.com", "password": "pass12345"})
    return app, c, tid, oid


@pytest.fixture
def tg(monkeypatch):
    """تليجرام متزيّف: بيسجل الرسايل ويرجّع message_id متسلسل."""
    out = {"sent": [], "next": 500}
    monkeypatch.setattr(telegram_bot, "TELEGRAM_BOT_TOKEN", "x")

    def get_id(chat_id, text, parse_mode="HTML"):
        out["next"] += 1
        out["sent"].append((chat_id, text, out["next"]))
        return out["next"]
    monkeypatch.setattr(telegram_bot, "send_message_get_id", get_id)
    monkeypatch.setattr(telegram_bot, "send_message",
                        lambda chat_id, text, parse_mode="HTML": out["sent"].append((chat_id, text, None)) or True)
    return out


def _fake_client(text):
    return B(messages=B(create=lambda **kw: B(content=[B(type="text", text=text)], stop_reason="end_turn")))


def _link(app, tid, chat="CHAT-1"):
    with app.app_context():
        t = Tenant.query.get(tid)
        t.questions_chat_id, t.questions_contact = chat, "محمد"
        db.session.commit()


def test_extract_always_strips_tag():
    assert questions.extract("هتأكد وأرجعلك 🙏 [ASK|مقاسات الطقم كام؟]") == ("هتأكد وأرجعلك 🙏", ["مقاسات الطقم كام؟"])
    assert questions.extract("الطقم بـ199") == ("الطقم بـ199", [])


def test_prompt_without_recipient_forbids_promises(env, monkeypatch):
    app, _, tid, _ = env
    with app.app_context():
        t = Tenant.query.get(tid)
        t.questions_chat_id = None
        db.session.commit()
        s = questions.prompt_section(t, [])
    assert "ماتوعديش إنك هترجعيله" in s and "[ASK|" not in s


def test_full_cycle_ask_alert_reply_memory_and_customer_notified(env, tg, monkeypatch):
    app, _, tid, _ = env
    _link(app, tid)
    sent_to_customers = []
    monkeypatch.setattr(bot_engine, "send_message", lambda b, psid, text, *a, **k: sent_to_customers.append((psid, text)))
    monkeypatch.setattr(bot_engine, "client", _fake_client("هتأكد من الفريق وأرجعلك هنا 🙏 [ASK|مقاسات قطع طقم الموكيت كام؟]"))
    with app.app_context():
        t = Tenant.query.get(tid)
        assert "[ASK|السؤال" in questions.prompt_section(t, [])
        b = bot_engine._serialize_tenant_bundle(t)
        for psid in ("c1", "c2"):     # عميلين نفس السؤال = سؤال واحد وتنبيه واحد
            st = bot_engine.default_state(tid, "PQ", "facebook")
            reply, _, _ = bot_engine.get_ai_response(b, psid, "سجاد المطبخ مقاساته كام", st)
            assert "[ASK" not in reply
            bot_engine.save_state(tid, psid, st)
        qs = BotQuestion.query.filter_by(tenant_id=tid).all()
        assert len(qs) == 1 and qs[0].status == "pending" and qs[0].product_key == "rug"
        assert qs[0].times_asked == 2 and len(json.loads(qs[0].askers)) == 2
        alerts = [x for x in tg["sent"] if x[2]]
        assert len(alerts) == 1 and alerts[0][0] == "CHAT-1" and "مقاسات قطع طقم الموكيت" in alerts[0][1]
        mid = alerts[0][2]

    # الفريق عمل Reply على التنبيه
    handled = questions.handle_telegram_message(
        {"chat": {"id": "CHAT-1"}, "text": "3 قطع: 50×80 و50×120 و50×160 سم",
         "reply_to_message": {"message_id": mid}}, app)
    assert handled
    with app.app_context():
        q = BotQuestion.query.filter_by(tenant_id=tid).first()
        assert q.status == "answered" and "50×160" in q.answer and q.askers == "[]"
        assert "50×160" in questions.prompt_section(Tenant.query.get(tid), [])
        st = bot_engine.load_state(tid, "c1")
        assert "50×160" in st["history"][-1]["content"]
    assert {p for p, _ in sent_to_customers} == {"c1", "c2"}
    assert all("رجعتلك بخصوص سؤالك" in m and "نجهزلك" in m for _, m in sent_to_customers)   # ليبي
    assert any("اتحفظت في ذاكرة البوت" in x[1] for x in tg["sent"])


def test_same_question_after_answer_doesnt_ping_team(env, tg, monkeypatch):
    app, _, tid, _ = env
    _link(app, tid)
    monkeypatch.setattr(bot_engine, "client", _fake_client("لحظة [ASK|كم مقاسات قطع طقم الموكيت]"))
    with app.app_context():
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        bot_engine.get_ai_response(b, "c3", "موكيت مقاسات", bot_engine.default_state(tid, "PQ", "facebook"))
        assert BotQuestion.query.filter_by(tenant_id=tid).count() == 1
    assert not [x for x in tg["sent"] if x[2]]


def test_demo_and_unlinked_never_register(env, tg, monkeypatch):
    app, _, tid, _ = env
    monkeypatch.setattr(bot_engine, "client", _fake_client("لحظة [ASK|لون جديد متوفر؟]"))
    with app.app_context():
        _link(app, tid)
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        st = bot_engine.default_state(tid, "demo", "demo")
        bot_engine.get_ai_response(b, "d1", "فيه لون أخضر؟", st)
        t = Tenant.query.get(tid)
        t.questions_chat_id = None
        db.session.commit()
        b = bot_engine._serialize_tenant_bundle(t)
        reply, _, _ = bot_engine.get_ai_response(b, "d2", "فيه لون أخضر؟", bot_engine.default_state(tid, "PQ", "facebook"))
        assert "[ASK" not in reply
        assert BotQuestion.query.filter(BotQuestion.question.like("%لون جديد%")).count() == 0


def test_link_code_and_unrelated_replies(env, tg):
    app, c, tid, _ = env
    code = c.post("/dashboard/telegram-questions/link-code", json={}).get_json()["code"]
    assert code.startswith("ASK-") and len(code) == 10
    assert questions.handle_telegram_message({"chat": {"id": "CHAT-9", "first_name": "سالم"}, "text": code.lower()}, app)
    with app.app_context():
        t = Tenant.query.get(tid)
        assert t.questions_chat_id == "CHAT-9" and t.questions_contact == "سالم" and t.questions_link_code is None
    assert questions.handle_telegram_message({"chat": {"id": "CHAT-9"}, "text": "ASK-NOPE00"}, app)  # كود غلط: اتعالج برسالة
    assert not questions.handle_telegram_message({"chat": {"id": "CHAT-9"}, "text": "اهلا"}, app)
    assert not questions.handle_telegram_message(
        {"chat": {"id": "CHAT-9"}, "text": "x", "reply_to_message": {"message_id": 99999}}, app)


def test_old_askers_outside_meta_window_skipped(env, monkeypatch):
    app, _, tid, _ = env
    sent = []
    monkeypatch.setattr(bot_engine, "send_message", lambda *a, **k: sent.append(a[1]))
    with app.app_context():
        old = time.time() - (questions.REPLY_WINDOW_HOURS + 1) * 3600
        q = BotQuestion(tenant_id=tid, question="فيه توصيل لغات؟", status="pending",
                        askers=json.dumps([{"psid": "late", "page_id": "PQ", "t": old},
                                           {"psid": "fresh", "page_id": "PQ", "t": time.time()}]))
        db.session.add(q)
        db.session.commit()
        assert questions.apply_answer(q, "أيوه، مجاني") == 1
    assert sent == ["fresh"]


def test_dashboard_page_json_only_and_isolated(env):
    app, c, tid, oid = env
    r = c.get("/dashboard/telegram-questions")
    assert r.status_code == 200 and "ذاكرة البوت" in r.get_data(as_text=True)
    assert c.post("/dashboard/telegram-questions/add", data={"question": "x", "answer": "y"}).status_code == 400
    d = c.post("/dashboard/telegram-questions/add",
               json={"question": "الخامة إيه؟", "answer": "بوليستر مقاوم للبقع", "product_key": "rug"}).get_json()
    fid = d["item"]["id"]
    assert d["ok"] and d["item"]["status"] == "answered"
    d = c.post(f"/dashboard/telegram-questions/{fid}", json={"status": "disabled"}).get_json()
    with app.app_context():
        assert "بوليستر" not in questions.prompt_section(Tenant.query.get(tid), [])   # الموقوفة مابتدخلش
        theirs = BotQuestion(tenant_id=oid, question="q", answer="a", status="answered")
        db.session.add(theirs)
        db.session.commit()
        theirs_id = theirs.id
    assert c.post(f"/dashboard/telegram-questions/{theirs_id}", json={"status": "disabled"}).status_code == 404
    assert c.post(f"/dashboard/telegram-questions/{theirs_id}/delete", json={}).status_code == 404
    assert c.post(f"/dashboard/telegram-questions/{fid}", json={"answer": "  "}).status_code == 400
    assert c.post(f"/dashboard/telegram-questions/{fid}/delete", json={}).get_json()["ok"]
    assert c.post("/dashboard/telegram-questions/unlink", json={}).get_json()["ok"]
