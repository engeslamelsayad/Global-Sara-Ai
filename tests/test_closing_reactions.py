"""
tests/test_closing_reactions.py — is_closing_reaction: إيماءات الإغلاق بس، مش كلمات.

اتكتب بعد bug حقيقي: «لا» و«تم» (كلمات عربي من حرفين) كانت بتتبلع كإيموجي.
"""
import os
import sys
import types

os.environ.setdefault("ANTHROPIC_API_KEY", "x")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import bot_engine

BUNDLE = {"bot_config": types.SimpleNamespace(closing_reactions='["👍","✅","🙏","👌","🤝","💪"]')}


@pytest.mark.parametrize("msg", ["👍", "✅", "🙏", " 👌 ", "❤️", "👍🏻", "🔥", "😍"])
def test_emoji_reactions_are_ignored(msg):
    assert bot_engine.is_closing_reaction(msg, BUNDLE)


@pytest.mark.parametrize("msg", ["لا", "تم", "اه", "آه", "إي", "اي", "ok", "no", "1", "2", "هو", "لا 👍", "نعم"])
def test_short_words_are_real_messages(msg):
    assert not bot_engine.is_closing_reaction(msg, BUNDLE)
