"""Tests for slash-command completion (the pure function behind the chat input's menu)."""

import pytest

from arcana_cli.tui.completion import SlashCompletions, slash_completions
from arcana_cli.ui.input_model import _SLASH_NAMES

AGENTS = ("scout", "scribe", "oracle")


def _complete(text: str) -> SlashCompletions:
    return slash_completions(text, _SLASH_NAMES, lambda: AGENTS)


def test_completes_command_names():
    assert _complete("/me") == SlashCompletions(start=0, items=("/memory",))


def test_a_bare_slash_offers_every_command():
    assert _complete("/").items == tuple(_SLASH_NAMES)


def test_completes_agent_names_after_switch():
    assert _complete("/switch sc") == SlashCompletions(start=len("/switch "), items=("scout", "scribe"))
    assert _complete("/switch ").items == AGENTS


@pytest.mark.parametrize("text", ["hello", "", " /me", "hi /switch sc", "/help me", "/card x"])
def test_completes_nothing_without_a_leading_slash_or_after_other_arguments(text):
    assert _complete(text).items == ()


def test_agent_names_are_only_read_for_a_switch_tail():
    calls: list[str] = []

    def names():
        calls.append("read")
        return AGENTS

    slash_completions("/sw", _SLASH_NAMES, names)
    slash_completions("hello", _SLASH_NAMES, names)
    assert calls == []
    slash_completions("/switch o", _SLASH_NAMES, names)
    assert calls == ["read"]
