"""
tests/test_google_sheet_script.py — كود Google Apps Script اللي التاجر بيحمّله:
بيتجهّز لكل tenant، JS سليم، وبيقرا نفس الحقول اللي push_to_google_sheet بيبعتها.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal

import pytest

import app_main
import bot_engine
import provisioning
from models import db, Product

TRICKY = 'متجر "ليبيا" */ </script>'


@pytest.fixture(scope="module")
def script():
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("gs-test", TRICKY, "gs@x.com", "pass12345")
        t.currency = "دينار"
        db.session.add(Product(tenant_id=t.id, product_key="neck", name="كريم الرقبة",
                               price_amount=Decimal("145"), is_active=True))
        db.session.commit()
    c = app.test_client()
    assert c.get("/dashboard/settings/general/google-sheet-script").status_code == 302   # لازم login
    c.post("/login", data={"email": "gs@x.com", "password": "pass12345"})
    r = c.get("/dashboard/settings/general/google-sheet-script")
    assert r.status_code == 200
    assert 'attachment; filename="sara-orders-gs-test.gs"' == r.headers["Content-Disposition"]
    return r.get_data(as_text=True)


def test_personalized_for_tenant(script):
    assert "كريم الرقبة" in script and "145 دينار" in script
    assert "getSpreadsheetTimeZone" in script and "Africa/Cairo" not in script


def test_tricky_business_name_cannot_break_the_js(script):
    head = script.split("*/", 1)[0]                     # أول تعليق لازم يفضل سليم
    assert "* /" in head
    assert 'business: "متجر \\"ليبيا\\" */ </script>"' in script   # JSON string صالح ومقروء


def test_valid_javascript(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node مش متسطّب")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
    try:
        assert subprocess.run([node, "--check", f.name], capture_output=True).returncode == 0
    finally:
        os.unlink(f.name)


def test_script_reads_every_field_the_bot_sends(script, monkeypatch):
    sent = {}
    monkeypatch.setattr(bot_engine.requests, "post", lambda url, json=None, timeout=None: sent.update(json))
    tenant = types.SimpleNamespace(google_sheet_url="https://x", business_name="B", slug="b")
    bot_engine.push_to_google_sheet(tenant, {"name": "n", "phone": "p", "address": "a", "product": "x",
                                             "price": "1", "discount": ""}, "PAGE")
    read = set(re.findall(r"data\.(\w+)", script))
    missing = set(sent) - read - {"business"}          # business مش عمود في الشيت (معلومة بس)
    assert not missing, f"الكود مابيقراش: {missing}"
