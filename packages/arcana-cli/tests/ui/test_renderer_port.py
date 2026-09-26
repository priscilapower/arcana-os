"""Tests for the renderer port value types and the renderer selection."""

import pytest
import typer

from arcana_cli._render import EXIT_ERROR
from arcana_cli.ui.renderer import (
    CANCELLED,
    YES_FLAG,
    Choice,
    JsonRenderer,
    NonInteractiveError,
    Question,
    Renderer,
    TtyRenderer,
    confirm_or_cancel,
    renderer_for,
)
from tests.support.renderer import RecordingRenderer


def test_renderer_for_json_is_the_json_adapter():
    assert isinstance(renderer_for(json=True), JsonRenderer)


def test_renderer_for_human_is_the_tty_adapter():
    assert isinstance(renderer_for(json=False), TtyRenderer)


def test_adapters_satisfy_the_protocol():
    adapters: list[Renderer] = [TtyRenderer(), JsonRenderer()]
    assert len(adapters) == 2


def test_question_repr_shows_a_plain_default():
    assert "default='gpt'" in repr(Question("Model", default="gpt"))


def test_question_repr_hides_a_secret_default():
    q = Question("API key", default="sk-live-123", secret=True)
    assert "sk-live-123" not in repr(q)
    assert "<hidden>" in repr(q)


def test_choice_defaults():
    c = Choice(3, "three")
    assert (c.value, c.label, c.preview, c.disabled) == (3, "three", None, False)


def test_non_interactive_error_is_an_exit_error():
    exc = NonInteractiveError("Agent name")
    assert isinstance(exc, typer.Exit)
    assert exc.exit_code == EXIT_ERROR
    assert str(exc) == "'Agent name' needs an answer, but --json mode never prompts"


def test_non_interactive_error_names_the_flag():
    exc = NonInteractiveError("Remove server?", flag="--yes")
    assert exc.flag == "--yes"
    assert str(exc).endswith("; pass --yes instead")


def test_non_interactive_error_reason_replaces_the_surface_clause():
    exc = NonInteractiveError("Delete?", flag="--yes", reason="the piped input ran out")
    assert str(exc) == "'Delete?' needs an answer, but the piped input ran out; pass --yes instead"


# ── confirm_or_cancel: the destructive-confirmation policy ────────────────


async def test_confirm_or_cancel_returns_on_yes():
    r = RecordingRenderer(confirms=[True])
    await confirm_or_cancel(r, "Delete agent 'scout'?")
    assert r.confirmations == ["Delete agent 'scout'?"]
    assert r.notes == []


async def test_confirm_or_cancel_on_no_says_cancelled_and_exits_1():
    r = RecordingRenderer(confirms=[False])
    with pytest.raises(typer.Exit) as exited:
        await confirm_or_cancel(r, "Delete agent 'scout'?")
    assert exited.value.exit_code == EXIT_ERROR
    assert r.notes_text().strip() == CANCELLED


async def test_confirm_or_cancel_defaults_to_no_and_names_yes():
    seen: dict[str, object] = {}

    class Spy(RecordingRenderer):
        async def confirm(self, text: str, *, default: bool = False, flag: str | None = None) -> bool:
            seen.update(default=default, flag=flag)
            return True

    await confirm_or_cancel(Spy(), "Remove?")
    assert seen == {"default": False, "flag": YES_FLAG}


async def test_confirm_or_cancel_fails_closed_under_json():
    with pytest.raises(NonInteractiveError) as refused:
        await confirm_or_cancel(JsonRenderer(), "Remove?")
    assert refused.value.flag == YES_FLAG
