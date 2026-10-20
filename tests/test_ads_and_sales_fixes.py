# -*- coding: utf-8 -*-
"""
ربط الإعلانات بالمنتجات (ads.py) + إصلاحات من مراجعة 158 محادثة حقيقية (أكتوبر 2026):
خصم الإعلان، «شكراً» في الحازم، اللهجة، تسريب التفكير الداخلي، التاريخ القديم، رقم التواصل الفاضي.
"""
import json
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

import ads
import app_main
import bot_engine
import dialect_text
import provisioning
import sales_playbook as sp
from models import db, AdProduct, BotConfig, Product, Tenant

B = types.SimpleNamespace


@pytest.fixture(scope="module")
def env():
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("ads-a", "آمان", "ads-a@x.com", "pass12345", dialect="ليبي")
        t.currency = "دينار"
        db.session.add(Product(tenant_id=t.id, product_key="rug", name="طقم موكيت بوهيمي للمطبخ",
                               keywords="موكيت,سجاد المطبخ,فرش المطبخ", price_note="199 بدل 280",
                               is_active=True))
        db.session.add(Product(tenant_id=t.id, product_key="caps", name="كبسولات Goldbloom",
                               keywords="كبسولات,سيروم", price_amount=Decimal("159"), is_active=True))
        other, _, _ = provisioning.create_tenant("ads-b", "B", "ads-b@x.com", "pass12345")
        db.session.commit()
        tid, oid = t.id, other.id
    c = app.test_client()
    c.post("/login", data={"email": "ads-a@x.com", "password": "pass12345"})
    return app, c, tid, oid


# ── ads.py: التعلم التلقائي + الوقوف عند التعارض ──

def test_auto_link_after_two_customers_then_conflict_stops_it(env):
    app, _, tid, _ = env
    with app.app_context():
        assert ads.record_hit(tid, "AD1", "New Sales Ad_Group_1", "u1") is None
        ads.vote(tid, "AD1", "rug", "u1")
        ads.vote(tid, "AD1", "rug", "u1")                      # نفس العميل = صوت واحد
        assert ads.record_hit(tid, "AD1", "", "u9") is None      # لسه صوت واحد
        mode, pk = ads.vote(tid, "AD1", "rug", "u2")
        assert (mode, pk) == ("auto", "rug")
        assert ads.record_hit(tid, "AD1", "", "u3") == "rug"
        ads.vote(tid, "AD1", "caps", "u4")                     # عميل واحد على منتج تاني: لسه مربوط
        assert ads.resolved_product_key(AdProduct.query.filter_by(ad_key="AD1").first()) == "rug"
        mode, pk = ads.vote(tid, "AD1", "caps", "u5")           # التاني: حملتين دخلوا على بعض
        assert (mode, pk) == ("conflict", None)
        assert ads.record_hit(tid, "AD1", "", "u6") is None
        ads.vote(tid, "AD1", "rug", "u7")                      # conflict مابيرجعش لوحده
        assert AdProduct.query.filter_by(ad_key="AD1").first().mode == "conflict"


def test_manual_beats_votes(env):
    app, _, tid, _ = env
    with app.app_context():
        ads.record_hit(tid, "AD2", "x", "m1")
        row = AdProduct.query.filter_by(ad_key="AD2").first()
        row.mode, row.product_key = "manual", "caps"
        db.session.commit()
        for u in ("m1", "m2", "m3"):
            ads.vote(tid, "AD2", "rug", u)
        assert ads.record_hit(tid, "AD2", "", "m4") == "caps"


def test_ad_key_prefers_id_over_title():
    assert ads.ad_key_from_referral({"ad_id": "123", "ads_context_data": {"ad_title": "T"}}) == ("123", "T")
    assert ads.ad_key_from_referral({"ads_context_data": {"ad_title": "T"}}) == ("title:T", "T")
    assert ads.ad_key_from_referral({})[0] is None


def _bundle(app, tid):
    with app.app_context():
        return bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))


def test_capture_referral_uses_learned_mapping(env):
    app, _, tid, _ = env
    with app.app_context():
        ads.record_hit(tid, "AD3", "New Sales Ad", "a1")
        ads.vote(tid, "AD3", "rug", "a1")
        ads.vote(tid, "AD3", "rug", "a2")
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        bot_engine.capture_ad_referral(b, {"recipient": {"id": "P"}, "referral": {
            "source": "ADS", "ad_id": "AD3", "ads_context_data": {"ad_title": "New Sales Ad"}}}, "a3")
        st = bot_engine.load_state(tid, "a3")
        assert st["source_ad_key"] == "AD3" and st["source_ad_product_key"] == "rug"


def _fake_client(captured, text="تمام"):
    def create(**kw):
        captured.append(kw)
        return B(content=[B(type="text", text=text)], stop_reason="end_turn")
    return B(messages=B(create=create))


def _system(kw):
    return "\n".join(b["text"] if isinstance(b, dict) else str(b) for b in kw["system"])


def test_unknown_ad_asks_for_screenshot_and_votes_from_customer_only(env, monkeypatch):
    app, _, tid, _ = env
    captured = []
    with app.app_context():
        ads.record_hit(tid, "AD4", "New Sales Ad_Group_1", "v1")
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        st = bot_engine.default_state(tid, "P", "facebook")
        st.update(source_ad_key="AD4", source_ad_title="New Sales Ad_Group_1", messages_count=1)
        # البوت خمّن الكبسولات من نفسه → مايتحسبش صوت
        monkeypatch.setattr(bot_engine, "client", _fake_client(captured, "الكبسولات بـ159 دينار"))
        bot_engine.get_ai_response(b, "v1", "السعر", st)
        assert "اطلبي منه في سطر واحد يبعت صورة الإعلان" in _system(captured[-1])
        assert not st.get("ad_vote_cast")
        # العميل سمّى المنتج → صوت
        bot_engine.get_ai_response(b, "v1", "سجاد المطبخ بكم", st)
        assert st["ad_vote_cast"] and st["source_ad_product_key"] == "rug"
        assert json.loads(AdProduct.query.filter_by(ad_key="AD4").first().votes) == {"rug": 1}


def test_ads_page_and_api_are_json_only_and_tenant_isolated(env):
    app, c, tid, oid = env
    with app.app_context():
        mine = AdProduct.query.filter_by(tenant_id=tid, ad_key="AD1").first().id
        ads.record_hit(oid, "OTHER", "x", "z1")
        theirs = AdProduct.query.filter_by(tenant_id=oid).first().id
    r = c.get("/dashboard/ads")
    assert r.status_code == 200 and "New Sales Ad_Group_1" in r.get_data(as_text=True)
    assert c.post(f"/dashboard/ads/{mine}", data={"product_key": "rug"}).status_code == 400
    assert c.post(f"/dashboard/ads/{theirs}", json={"product_key": "rug"}).status_code == 404
    assert c.post(f"/dashboard/ads/{mine}", json={"product_key": "nope"}).status_code == 400
    d = c.post(f"/dashboard/ads/{mine}", json={"product_key": "rug"}).get_json()
    assert d["ok"] and d["ad"]["mode"] == "manual" and d["ad"]["product_key"] == "rug"
    d = c.post(f"/dashboard/ads/{mine}", json={"mode": "auto"}).get_json()
    assert d["ad"]["mode"] == "auto" and d["ad"]["product_key"] is None and d["ad"]["votes"] == {}


# ── خصم الإعلان ──

def test_price_offer_parsing():
    P = lambda note, amt=None: B(price_note=note, price_amount=amt)
    assert bot_engine._price_offer(P("199 بدل 280")) == (199, 280, 29)
    assert bot_engine._price_offer(P("١٩٩ دينار بدلاً من ٢٨٠")) == (199, 280, 29)
    assert bot_engine._price_offer(P("بدل 280", Decimal("199"))) == (199, 280, 29)
    assert bot_engine._price_offer(P("199 دينار شامل التوصيل")) is None
    assert bot_engine._price_offer(P("300 بدل 280")) is None


def test_offer_reaches_prompt(env, monkeypatch):
    app, _, tid, _ = env
    captured = []
    monkeypatch.setattr(bot_engine, "client", _fake_client(captured))
    with app.app_context():
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        st = bot_engine.default_state(tid, "P", "facebook")
        bot_engine.get_ai_response(b, "o1", "الإعلان فيه خصم على الموكيت؟", st)
    s = _system(captured[-1])
    assert "خصم 29%" in s and "ماتقوليش «مفيش خصم»" in s


# ── «شكراً» في الحازم ──

@pytest.mark.parametrize("msg", ["شكرا بارك الله فيك", "شكراً", "مشكور", "باهي صحيت انزيد انشوف",
                                 "خليني نشوف", "تسلم 🙏"])
def test_soft_exits(msg):
    assert bot_engine.is_soft_exit(msg)


@pytest.mark.parametrize("msg", ["ممكن نشوف الالوان", "شكرا، كم المقاس؟", "تم", "لا",
                                 "شكرا ابعتلك الاسم والرقم توا وبعدين العنوان كامل بالتفصيل"])
def test_not_soft_exits(msg):
    assert not bot_engine.is_soft_exit(msg)


def test_soft_exit_tries_once_on_assertive_only(env, monkeypatch):
    app, _, tid, _ = env
    captured = []
    monkeypatch.setattr(bot_engine, "client", _fake_client(captured))
    with app.app_context():
        bc = BotConfig.query.filter_by(tenant_id=tid).first()
        for style, expect in (("assertive", True), ("balanced", False)):
            bc.sales_style = style
            db.session.commit()
            b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
            st = bot_engine.default_state(tid, "P", "facebook")
            st.update(price_quoted=True, stage="INTERESTED", messages_count=4)
            bot_engine.get_ai_response(b, "s-" + style, "شكرا بارك الله فيك", st)
            assert ("ده تردد متخفّي" in _system(captured[-1])) is expect
            if expect:   # المرة التانية: مفيش محاولة تانية
                bot_engine.get_ai_response(b, "s-" + style, "شكرا", st)
                assert "ده تردد متخفّي" not in _system(captured[-1])


# ── اللهجة ──

def test_libyan_swaps_and_neutral_gender(env, monkeypatch):
    h = dialect_text.vocab_hint("ليبي")
    assert "ده/دي ← هذا/هذي" in h and "عشان ← باش" in h
    assert "بدّلي" not in dialect_text.vocab_hint("مصري")
    app, _, tid, _ = env
    captured = []
    monkeypatch.setattr(bot_engine, "client", _fake_client(captured))
    with app.app_context():
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        bot_engine.get_ai_response(b, "g1", "مرحبا", bot_engine.default_state(tid, "P", "facebook"))
    assert "صيغة المذكر العامة" in _system(captured[-1])


# ── التفكير الداخلي ماوصلش للعميل ──

def test_strip_meta_paragraph():
    leaked = ('العميل جاي من إعلان وسأل "السعر" بس، حسب القواعد سؤال تأهيل واحد. فأنسب رد: سؤال.\n\n'
              "شنو المنتج اللي شفته في الإعلان؟")
    assert bot_engine._strip_meta(leaked) == "شنو المنتج اللي شفته في الإعلان؟"
    assert bot_engine._strip_meta("الطقم بـ199 دينار. تبيه؟") == "الطقم بـ199 دينار. تبيه؟"


# ── رقم التواصل الفاضي ──

def test_empty_contact_number_never_says_undefined(env, monkeypatch):
    app, _, tid, _ = env
    captured = []
    monkeypatch.setattr(bot_engine, "client", _fake_client(captured))
    with app.app_context():
        bc = BotConfig.query.filter_by(tenant_id=tid).first()
        bc.contact_number = ""
        db.session.commit()
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(tid))
        bot_engine.get_ai_response(b, "c1", "نبي نرجع المنتج", bot_engine.default_state(tid, "P", "facebook"))
    s = _system(captured[-1])
    assert "الرسمي: غير محدد" not in s and "على الرقم الرسمي" not in s
    assert "مع فريق المتجر هنا في نفس المحادثة" in s
    assert "ماتذكريش أي رقم" in s


# ── صفحة المتابعات بتبين الحالة الحقيقية ──

def test_followups_on_off_comes_from_stages_only(env):
    """مفيش خانة في السياسات تاني: المتابعات شغالة = فيه مرحلة مفعّلة."""
    app, c, tid, _ = env
    from models import FollowupStage
    assert "enable_followup" not in c.get("/dashboard/settings/policy").get_data(as_text=True)
    with app.app_context():
        FollowupStage.query.filter_by(tenant_id=tid).update({"is_active": False})
        db.session.commit()
    assert "المتابعات واقفة دلوقتي" in c.get("/dashboard/followups").get_data(as_text=True)
    with app.app_context():
        st = FollowupStage.query.filter_by(tenant_id=tid).first()
        st.is_active = True
        db.session.commit()
    assert "المتابعات واقفة دلوقتي" not in c.get("/dashboard/followups").get_data(as_text=True)


def test_migration_keeps_tenants_that_had_followups_off_silent(env):
    """حساب كان قافل الخانة: الـ migration بتوقف مراحله بدل ما المتابعات تشتغل عنده فجأة."""
    app, _, tid, oid = env
    import migrate_add_columns
    from models import FollowupStage, Policy
    with app.app_context():
        pol = Policy.query.filter_by(tenant_id=oid).first()
        pol.enable_followup = False
        db.session.commit()
        assert FollowupStage.query.filter_by(tenant_id=oid, is_active=True).count() > 0
        mine_before = FollowupStage.query.filter_by(tenant_id=tid, is_active=True).count()
    migrate_add_columns.run(app)
    with app.app_context():
        assert FollowupStage.query.filter_by(tenant_id=oid, is_active=True).count() == 0
        assert Policy.query.filter_by(tenant_id=oid).first().enable_followup is True
        assert FollowupStage.query.filter_by(tenant_id=tid, is_active=True).count() == mine_before


# ── محادثة راجعة بعد غياب طويل = من غير التاريخ القديم (الدولار والشامي من أيام سوريا) ──

def test_stale_history_is_dropped(env, monkeypatch):
    app, _, tid, _ = env
    captured, sent = [], []
    monkeypatch.setattr(bot_engine, "client", _fake_client(captured))
    monkeypatch.setattr(bot_engine, "send_message", lambda *a, **k: sent.append(a[2]))
    with app.app_context():
        from models import Page
        db.session.add(Page(tenant_id=tid, page_id="PH", platform="facebook", access_token="t", is_active=True))
        db.session.commit()
        for psid, age_h, keep in (("h-old", bot_engine.HISTORY_TTL_HOURS + 1, False), ("h-new", 2, True)):
            st = bot_engine.default_state(tid, "PH", "facebook")
            st.update(history=[{"role": "user", "content": "سعر"},
                               {"role": "assistant", "content": "25 دولار بدل 37 🙌"}],
                      last_message=time.time() - age_h * 3600, price_quoted=True, stage="INTERESTED")
            bot_engine.save_state(tid, psid, st)
            bot_engine.do_process_message(tid, psid, "مرحبا", "PH", "facebook")
            msgs = captured[-1]["messages"]
            assert any("25 دولار" in str(m.get("content")) for m in msgs) is keep
