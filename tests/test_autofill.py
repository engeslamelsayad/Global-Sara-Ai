"""
tests/test_autofill.py — «✨ إعداد البوت تلقائياً»: السياق اللي بيروح للـ AI، تنضيف الرد،
وتطبيق القواعد (إيقاف القديمة مش مسحها + عزل الـ tenants). الـ AI متزيّف — مفيش استدعاء حقيقي.
"""
import json
import os
import sys
import types

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

import pytest

import ai_assist
import app_main
import provisioning
from models import db, Product, SmartRule

B = types.SimpleNamespace
AI_OUT = {
    "bot_name": "سارة", "bot_age": 99, "persona": "سارة بتبيع منتجات آمان ستور.", "tone": "ودودة",
    "forbidden_words": ["دلوقتي", ""], "forbidden_openers": ["كيف يمكنني مساعدتك؟"],
    "objection_expensive": "x", "objection_unsure": "y", "objection_later": "z",
    "smart_rules": [{"category": "safety", "text": "من غير تشخيص."}, {"category": "bogus", "text": "توصيل مجاني."},
                    {"category": "sales", "text": ""}] + [{"category": "sales", "text": f"r{i}"} for i in range(10)],
    "notes": ["ناقص سعر"],
}


@pytest.fixture(scope="module")
def env():
    calls = []

    def fake_create(**kw):
        calls.append(kw)
        return B(content=[B(type="thinking", thinking=""),
                          B(type="text", text=json.dumps(AI_OUT, ensure_ascii=False))], stop_reason="end_turn")

    ai_assist.client.messages.create = fake_create
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("af-a", "آمان", "af-a@x.com", "pass12345", dialect="ليبي")
        t.business_description = "متجر في ليبيا"
        t.currency = "دينار"
        db.session.add(Product(tenant_id=t.id, product_key="neck", name="كريم الرقبة",
                               price_amount=Decimal("145"), is_active=True))
        db.session.add(SmartRule(tenant_id=t.id, rule_text="قديمة", category="sales", is_active=True))
        provisioning.create_tenant("af-b", "B", "af-b@x.com", "pass12345")
        db.session.commit()
        tid = t.id
    c = app.test_client()
    c.post("/login", data={"email": "af-a@x.com", "password": "pass12345"})
    return app, c, tid, calls


def test_autofill_sends_full_context_and_sanitizes(env):
    _, c, _, calls = env
    r = c.post("/dashboard/settings/bot/ai-autofill", json={"dialect": "ليبي", "sales_style": "assertive"})
    d = r.get_json()
    assert r.status_code == 200
    prompt = calls[-1]["messages"][0]["content"]
    assert "كريم الرقبة — السعر: 145 دينار" in prompt          # المنتجات بعملة الـ tenant
    assert "assertive" in prompt and "ليبي" in prompt and "مدة التوصيل" in prompt
    assert "ممنوع صيغة" in prompt and "سيب الحقل بتاعها فاضي" in prompt   # أسلوب الكتابة + الحقايق بس
    assert calls[-1]["model"] == ai_assist.AUTOFILL_MODEL
    assert d["bot_age"] == 60 and d["forbidden_words"] == ["دلوقتي"]
    assert len(d["smart_rules"]) == ai_assist.AUTOFILL_MAX_RULES          # الحد بعد شيل الفاضي
    assert all(r["text"] for r in d["smart_rules"])
    assert d["smart_rules"][1]["category"] == "custom"                     # تصنيف غلط → custom


def test_apply_rules_pauses_old_and_is_tenant_isolated(env):
    app, c, tid, _ = env
    r = c.post("/dashboard/settings/bot/ai-autofill/apply-rules",
               json={"rules": AI_OUT["smart_rules"][:2], "replace": True}).get_json()
    assert r == {"ok": True, "added": 2, "paused": 1}
    with app.app_context():
        rows = SmartRule.query.filter_by(tenant_id=tid).all()
        assert sum(x.is_active for x in rows) == 2
        assert any(x.rule_text == "قديمة" and not x.is_active for x in rows)   # اتوقفت، ماتمسحتش
    other = app.test_client()
    other.post("/login", data={"email": "af-b@x.com", "password": "pass12345"})
    other.post("/dashboard/settings/bot/ai-autofill/apply-rules",
               json={"rules": [{"category": "sales", "text": "x"}], "replace": True})
    with app.app_context():
        assert SmartRule.query.filter_by(tenant_id=tid, is_active=True).count() == 2


def test_apply_rules_rejects_bad_requests(env):
    _, c, _, _ = env
    assert c.post("/dashboard/settings/bot/ai-autofill/apply-rules", data={"rules": "x"}).status_code == 415
    assert c.post("/dashboard/settings/bot/ai-autofill/apply-rules", json={"rules": []}).status_code == 400
