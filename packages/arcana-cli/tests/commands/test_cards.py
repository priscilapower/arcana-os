"""Tests for arcana cards commands."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import arcana_cli.tui.card_picker as card_picker_app
import arcana_cli.ui.renderer.tty as tty_mod
from arcana.types.card import Card
from arcana_cli._render import EXIT_ERROR
from arcana_cli.commands.cards import list_cards, show_card
from arcana_cli.main import app
from arcana_cli.ui.renderer import Choice
from tests.support.renderer import RecordingRenderer

runner = CliRunner()

GOLDEN = Path(__file__).parent / "golden" / "cards"
# Pin the console width and keep colour off so the recorded output is stable.
GOLDEN_ENV: dict[str, str | None] = {"COLUMNS": "100", "FORCE_COLOR": None, "TTY_COMPATIBLE": None}


def test_cards_browse_without_a_terminal_fails_closed():
    # The picker needs a terminal: piped, it refuses and points at `cards show`.
    result = runner.invoke(app, ["cards"], input="the-fool\n")
    assert result.exit_code == EXIT_ERROR
    assert "never prompts" in result.output
    assert "arcana cards show" in result.output
    assert "Explorer" not in result.output


def test_cards_browse_on_a_terminal_opens_the_picker_and_shows_the_pick(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    async def fake_pick(choices: Sequence[Choice[Card]], **kwargs: Any) -> list[int]:
        seen.update(values=[c.value for c in choices], **kwargs)
        return [next(i for i, c in enumerate(choices) if c.value is Card.FOOL)]

    monkeypatch.setattr(tty_mod, "_is_terminal", lambda: True)
    monkeypatch.setattr(card_picker_app, "pick", fake_pick)
    result = runner.invoke(app, ["cards"])
    assert result.exit_code == 0, result.output
    assert "Explorer" in result.output
    assert "0.95" in result.output
    assert Card.WORLD not in seen["values"]
    assert len(seen["values"]) == len(Card) - 1
    assert seen["title"] == "Browse the Major Arcana"


def test_cards_show_by_key():
    result = runner.invoke(app, ["cards", "show", "the-hermit"])
    assert result.exit_code == 0
    assert "Hermit" in result.output
    assert "Researcher" in result.output or "Analyst" in result.output


def test_cards_show_by_short_name():
    result = runner.invoke(app, ["cards", "show", "hermit"])
    assert result.exit_code == 0
    assert "Hermit" in result.output


def test_cards_show_includes_prompt_ingredients():
    result = runner.invoke(app, ["cards", "show", "the-fool"])
    assert result.exit_code == 0
    assert "Tone" in result.output or "tone" in result.output
    assert "Priorities" in result.output or "priorities" in result.output


def test_cards_show_includes_memory_weights():
    result = runner.invoke(app, ["cards", "show", "the-fool"])
    assert result.exit_code == 0
    assert "Memory" in result.output
    assert "episodic" in result.output


def test_cards_show_includes_synergies():
    result = runner.invoke(app, ["cards", "show", "the-fool"])
    assert result.exit_code == 0
    assert "Synerg" in result.output


def test_cards_show_unknown_card_exits_nonzero():
    result = runner.invoke(app, ["cards", "show", "not-a-real-card"])
    assert result.exit_code != 0


# ── golden output: byte-identical to the pre-renderer command ─────────────


@pytest.mark.parametrize(
    ("golden", "args", "stdin"),
    [
        ("show_the_hermit", ["cards", "show", "the-hermit"], None),
        ("show_hermit", ["cards", "show", "hermit"], None),
        ("show_unknown", ["cards", "show", "not-a-real-card"], None),
        ("show_ambiguous", ["cards", "show", "the"], None),
        ("browse_non_tty", ["cards"], None),
    ],
)
def test_cards_output_matches_golden(golden: str, args: list[str], stdin: str | None):
    result = runner.invoke(app, args, input=stdin, env=GOLDEN_ENV)
    expected = (GOLDEN / f"{golden}.txt").read_text()
    assert f"exit={result.exit_code}\n{result.output}" == expected


# ── the renderer-agnostic command bodies ──────────────────────────────────


async def test_list_cards_offers_every_card_but_the_world():
    r = RecordingRenderer(selections=[None])
    await list_cards(r)
    (offered,) = r.offered
    values = [c.value for c in offered]
    assert Card.WORLD not in values
    assert len(values) == len(Card) - 1
    assert all(c.preview is not None for c in offered)


async def test_list_cards_shows_the_picked_card():
    r = RecordingRenderer(selections=[Card.HERMIT])
    await list_cards(r)
    assert "IX · The Hermit" in r.text()


async def test_list_cards_cancel_emits_nothing():
    r = RecordingRenderer(selections=[None])
    await list_cards(r)
    assert r.emitted == []


async def test_show_card_emits_the_panel():
    r = RecordingRenderer()
    await show_card(r, "hermit")
    assert "Researcher / Deep Analyst" in r.text()


async def test_show_card_unknown_emits_error_and_exits():
    r = RecordingRenderer()
    with pytest.raises(typer.Exit) as exc:
        await show_card(r, "not-a-real-card")
    assert exc.value.exit_code == EXIT_ERROR
    assert "Unknown card: 'not-a-real-card'" in r.text()


async def test_show_card_ambiguous_names_the_matches():
    r = RecordingRenderer()
    with pytest.raises(typer.Exit):
        await show_card(r, "the")
    assert "Ambiguous: The Fool" in r.text()
