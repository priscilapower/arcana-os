"""Tests for arcana cards commands."""

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


def test_cards_browse_lists_all_22_in_non_tty():
    # non-TTY fallback: prints card names, blank input cancels
    result = runner.invoke(app, ["cards"], input="\n")
    assert result.exit_code == 0
    assert "The Fool" in result.output
    assert "The World" in result.output


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
