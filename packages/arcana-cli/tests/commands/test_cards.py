"""Tests for arcana cards commands."""

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from arcana.types.card import Card
from arcana_cli._render import EXIT_ERROR
from arcana_cli.commands.cards import list_cards, show_card
from arcana_cli.main import app
from tests.support.renderer import RecordingRenderer

runner = CliRunner()

GOLDEN = Path(__file__).parent / "golden" / "cards"
# Pin the console width and keep colour off so the recorded output is stable.
GOLDEN_ENV: dict[str, str | None] = {"COLUMNS": "100", "FORCE_COLOR": None, "TTY_COMPATIBLE": None}


def test_cards_browse_lists_all_21_in_non_tty():
    # non-TTY fallback: prints card names, blank input cancels
    result = runner.invoke(app, ["cards"], input="\n")
    assert result.exit_code == 0
    assert "The Fool" in result.output
    assert "The Magician" in result.output
    assert "The High Priestess" in result.output
    assert "The Empress" in result.output
    assert "The Emperor" in result.output
    assert "The Hierophant" in result.output
    assert "The Lovers" in result.output
    assert "The Chariot" in result.output
    assert "Strength" in result.output
    assert "The Hermit" in result.output
    assert "Wheel of Fortune" in result.output
    assert "Justice" in result.output
    assert "The Hanged Man" in result.output
    assert "Death" in result.output
    assert "Temperance" in result.output
    assert "The Devil" in result.output
    assert "The Star" in result.output
    assert "The Moon" in result.output
    assert "The Sun" in result.output
    assert "Judgement" in result.output


def test_cards_browse_selecting_card_shows_details():
    # non-TTY fallback: selecting a card by key prints its full panel
    result = runner.invoke(app, ["cards"], input="the-fool\n")
    assert result.exit_code == 0
    assert "Explorer" in result.output
    assert "0.95" in result.output


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
        ("browse_pick_fool", ["cards"], "the-fool\n"),
        ("browse_cancel", ["cards"], "\n"),
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
