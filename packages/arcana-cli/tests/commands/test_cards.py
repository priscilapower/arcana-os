"""Tests for arcana cards commands."""

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


def test_cards_list_shows_all_22():
    result = runner.invoke(app, ["cards"])
    assert result.exit_code == 0
    assert "The Fool" in result.output
    assert "The World" in result.output
    assert "22" in result.output or "XXI" in result.output


def test_cards_list_shows_archetype_and_temp():
    result = runner.invoke(app, ["cards"])
    assert result.exit_code == 0
    assert "Role" in result.output or "Archetype" in result.output or "Explorer" in result.output
    assert "0.95" in result.output  # The Fool's temperature


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
