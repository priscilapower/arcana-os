"""Tests for arcana cards commands."""

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


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
