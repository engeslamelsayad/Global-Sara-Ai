# -*- coding: utf-8 -*-
"""
صور المنتج (من logs 20 أكتوبر): العميل كتب «صورة له» والبوت قال «ما عنديش صورة أبعثها»
وبعدها بثواني الكود بعت الصورة — والطلب التاني مكانش بيبعت حاجة.
"""
import os
import sys
import types

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import bot_engine

IMGS = "https://x.com/1.jpg, https://x.com/2.jpg,https://x.com/3.jpg\nhttps://x.com/4.jpg"
P = types.SimpleNamespace(product_key="rug", name="طقم موكيت", image_urls=IMGS, is_active=True,
                          cross_selling="", product_link="", price_note="199", price_amount=None)


@pytest.mark.parametrize("msg", ["صورة له", "السعر وصورة له", "صورة اخي ممكن", "ابعتلي صور",
                                 "عندك صور حقيقية؟", "وريني صوره", "في صور"])
def test_photo_requests(msg):
    assert bot_engine.is_photo_request(msg)


@pytest.mark.parametrize("msg", ["[صورة]", "نفس الصورة طاقم بوهيمي المطبخ ثلاث قطع",
                                 "شفت الصورة في الإعلان وعجبني الطقم كثير", "بكم", "", None])
def test_not_photo_requests(msg):
    assert not bot_engine.is_photo_request(msg)


def test_image_list():
    assert bot_engine._image_list(P) == [f"https://x.com/{i}.jpg" for i in range(1, 5)]


def _run(monkeypatch, msg, state):
    sent = []
    bundle = {"keywords": [], "products": [P], "bot_config": None, "pages": {},
              "tenant": types.SimpleNamespace(id=1, currency="دينار")}
    monkeypatch.setattr(bot_engine, "get_tenant_for_page", lambda p: bundle)
    monkeypatch.setattr(bot_engine, "load_state", lambda t, s: state)
    monkeypatch.setattr(bot_engine, "save_state", lambda *a: None)
    monkeypatch.setattr(bot_engine, "send_message", lambda *a, **k: True)
    monkeypatch.setattr(bot_engine, "send_image", lambda b, s, u, *a: sent.append(u) or True)
    monkeypatch.setattr(bot_engine, "apply_stage_labels", lambda *a, **k: None)
    monkeypatch.setattr(bot_engine.time, "sleep", lambda s: None)

    def fake_ai(bundle, sid, m, st, **kw):
        st["photo_request"] = bot_engine.is_photo_request(m)
        return "تمام", None, P
    monkeypatch.setattr(bot_engine, "get_ai_response", fake_ai)
    bot_engine.do_process_message("tenant-1", "u1", msg, "PAGE", "facebook")
    return sent


def test_first_image_once_then_more_on_request(monkeypatch):
    st = bot_engine.default_state(1, "PAGE", "facebook")
    assert _run(monkeypatch, "بكم الموكيت", st) == ["https://x.com/1.jpg"]
    assert _run(monkeypatch, "بكم", st) == []                      # مايتكررش من غير طلب
    assert _run(monkeypatch, "صورة له", st) == ["https://x.com/2.jpg", "https://x.com/3.jpg",
                                                 "https://x.com/4.jpg"]
    assert _run(monkeypatch, "ابعتلي صور", st) == ["https://x.com/1.jpg", "https://x.com/2.jpg",
                                                    "https://x.com/3.jpg"]   # خلصوا → من الأول
    assert "photo_request" not in st


def test_prompt_knows_photos_are_sent(monkeypatch):
    import app_main
    import provisioning
    from models import db, Product, Tenant
    cap = []

    def create(**kw):
        cap.append("\n".join(b["text"] for b in kw["system"]))
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="تمام")],
                                     stop_reason="end_turn")
    monkeypatch.setattr(bot_engine, "client",
                        types.SimpleNamespace(messages=types.SimpleNamespace(create=create)))
    with app_main.app.app_context():
        t, _, _ = provisioning.create_tenant("photos", "P", "photos@x.com", "pass12345")
        db.session.add(Product(tenant_id=t.id, product_key="rug", name="طقم موكيت", keywords="موكيت",
                               price_note="199", image_urls=IMGS, is_active=True))
        db.session.commit()
        b = bot_engine._serialize_tenant_bundle(Tenant.query.get(t.id))
        st = bot_engine.default_state(t.id, "P", "facebook")
        bot_engine.get_ai_response(b, "u1", "الموكيت صورة له", st)
        assert st["photo_request"] and "النظام هيبعتله صور المنتج" in cap[-1]
        st2 = bot_engine.default_state(t.id, "P", "facebook")
        bot_engine.get_ai_response(b, "u2", "بكم الموكيت", st2)
        assert not st2["photo_request"] and "ماتقوليش إن مفيش صورة" in cap[-1]
