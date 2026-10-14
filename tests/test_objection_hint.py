# -*- coding: utf-8 -*-
"""تلميح الاعتراض لازم يحترم حد محاولات كل أسلوب — المتوازن كان بيودّع العميل من أول «غالي»."""
import sales_playbook as sp

FAREWELL_BAN = "ممنوع توديع"


def test_balanced_first_objection_forbids_giving_up():
    h = sp.objection_hint("balanced", 1)
    assert FAREWELL_BAN in h and "لحد 2" in h


def test_balanced_second_objection_is_last_attempt():
    h = sp.objection_hint("balanced", 2)
    assert FAREWELL_BAN not in h and "آخر محاولة" in h and "ممنوع تتكرر" in h


def test_assertive_keeps_pushing_until_third():
    assert FAREWELL_BAN in sp.objection_hint("assertive", 2)
    assert "آخر محاولة" in sp.objection_hint("assertive", 3)


def test_calm_never_pushes():
    h = sp.objection_hint("calm", 1)
    assert "من غير ضغط" in h and "محاولات" not in h


def test_unknown_style_falls_back_to_balanced():
    assert sp.objection_hint("weird", None) == sp.objection_hint("balanced", 1)


def test_balanced_block_bans_first_objection_exit():
    pb = sp.playbook("balanced")
    assert "أول اعتراض عمره ما يكون نهاية المحادثة" in pb
    assert "[🛑 إمتى تقفي]" in pb
    assert "أول اعتراض عمره" not in sp.playbook("calm")


# ── الرفض الصريح («لا») — من محادثة حقيقية على الحازم: البوت ودّع العميل من أول «لا» ──

def test_first_refusal_is_not_the_end_for_assertive_and_balanced():
    for style in ("assertive", "balanced"):
        h = sp.objection_hint(style, 1, refusals=1)
        assert "مش نهاية" in h and "ممنوع توديع" in h


def test_second_refusal_stops():
    for style in ("assertive", "balanced"):
        assert "وقفي المحاولة" in sp.objection_hint(style, 3, refusals=2)


def test_calm_respects_first_refusal():
    assert "احترمي قراره فوراً" in sp.objection_hint("calm", 1, refusals=1)


def test_assertive_price_hint_bans_yes_no_close():
    assert "سؤال آه/لا" in sp.price_hint("assertive")
    assert "إغلاق افتراضي" in sp.price_hint("assertive")
    assert "إغلاق افتراضي" not in sp.price_hint("calm")
