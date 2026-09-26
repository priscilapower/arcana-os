"""Tests for the renderer port value types and the renderer selection."""

import typer

from arcana_cli._render import EXIT_ERROR
from arcana_cli.ui.renderer import (
    Choice,
    JsonRenderer,
    NonInteractiveError,
    Question,
    Renderer,
    TtyRenderer,
    renderer_for,
)


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
