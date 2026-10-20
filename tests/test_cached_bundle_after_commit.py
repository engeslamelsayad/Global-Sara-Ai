# -*- coding: utf-8 -*-
"""
Regression (production، 20 أكتوبر 2026): عميلة ضغطت على إعلان وكتبت «السعر من فضلك» والبوت مارد.
Meta بتبعت ضغطة الإعلان في طلب لوحده → ads.record_hit بيعمل commit → كائنات الـ tenant
المتخزنة في الكاش اتعملها expire → الطلب خلص والـ session اتقفلت → طلب الرسالة لقى الكاش
فيه كائنات detached منتهية → DetachedInstanceError في buffer_message والعميل مااترّدش عليه.
"""
import os
import sys

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_main
import bot_engine
import provisioning
import webhook
from models import db, Page


def test_ad_click_then_message_is_buffered(monkeypatch):
    app = app_main.app
    with app.app_context():
        t, _, _ = provisioning.create_tenant("cache-a", "C", "cache-a@x.com", "pass12345")
        db.session.add(Page(tenant_id=t.id, page_id="PC", platform="facebook", access_token="t", is_active=True))
        db.session.commit()
    bot_engine.invalidate_tenant_cache()
    buffered, errors = [], []
    # زي buffer_message الحقيقي: بيقرا إعدادات البوت من الـ bundle المتخزن
    monkeypatch.setattr(webhook, "buffer_message",
                        lambda bundle, psid, msg, *a, **k: buffered.append((psid, msg, bundle["bot_config"].debounce_seconds)))
    real_print = print
    monkeypatch.setattr("builtins.print", lambda *a, **k: (errors.append(" ".join(map(str, a))) if "Webhook error" in " ".join(map(str, a)) else None) or real_print(*a, **k))
    c = app.test_client()
    ref = {"source": "ADS", "type": "OPEN_THREAD", "ad_id": "AD-CACHE",
           "ads_context_data": {"ad_title": "New Sales Ad_Group_1"}}
    # 1) ضغطة الإعلان لوحدها (طلب منفصل)
    c.post("/webhook", json={"object": "page", "entry": [{"id": "PC", "messaging": [
        {"sender": {"id": "z1"}, "recipient": {"id": "PC"}, "timestamp": 1, "referral": ref}]}]})
    # 2) رسالة العميلة (طلب تاني، الكاش لسه شغال)
    c.post("/webhook", json={"object": "page", "entry": [{"id": "PC", "messaging": [
        {"sender": {"id": "z1"}, "recipient": {"id": "PC"}, "timestamp": 2,
         "message": {"mid": "m", "text": "السعر من فضلك", "referral": ref}}]}]})
    assert not errors, errors
    assert buffered and buffered[-1][:2] == ("z1", "السعر من فضلك")
