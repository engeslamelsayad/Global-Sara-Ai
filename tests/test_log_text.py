# -*- coding: utf-8 -*-
"""نص المحادثة في logs Railway: أرقام الموبايل متخفية، والأسعار سليمة."""
import os
import sys

os.environ.setdefault("DISABLE_SCHEDULER", "1")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from bot_engine import log_text


@pytest.mark.parametrize("phone, tail", [
    ("0912345678", "78"), ("+218 91 234 5678", "78"), ("091-234-5678", "78"),
    ("٠٩١٢٣٤٥٦٧٨", "٧٨"), ("01012345678", "78"),
])
def test_phones_are_masked(phone, tail):
    out = log_text(f"اسمي أحمد ورقمي {phone} والعنوان طرابلس")
    assert "📵***" + tail in out
    assert phone not in out and "234" not in out.replace("📵", "")


def test_prices_and_short_numbers_stay():
    out = log_text("السعر 159 دينار، 30 كبسولة، التوصيل 2-3 أيام، 1500 جنيه")
    assert out == "السعر 159 دينار، 30 كبسولة، التوصيل 2-3 أيام، 1500 جنيه"


def test_single_line_and_truncated():
    out = log_text("سطر\nتاني " + "ا" * 400)
    assert "\n" not in out and " ⏎ " in out
    assert len(out) == 301 and out.endswith("…")


def test_empty():
    assert log_text(None) == "" and log_text("") == ""
