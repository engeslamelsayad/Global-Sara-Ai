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
