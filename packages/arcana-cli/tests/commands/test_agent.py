"""Tests for arcana agent commands."""

from pathlib import Path

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


def test_agent_list_no_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    assert "No agents yet" in result.output


def test_agent_show():
    result = runner.invoke(app, ["agent", "show", "scout"])
    assert result.exit_code == 0
    assert "scout" in result.output


def test_agent_delete_with_yes_flag():
    result = runner.invoke(app, ["agent", "delete", "scout", "--yes"])
    assert result.exit_code == 0
    assert "scout" in result.output


def test_agent_create_with_flags():
    result = runner.invoke(
        app,
        ["agent", "create", "--name", "scout", "--card", "the-hermit", "--model", "ollama/hermes-3"],
    )
    assert result.exit_code == 0
    assert "scout" in result.output


def test_agent_create_unknown_card_exits_nonzero():
    result = runner.invoke(
        app,
        ["agent", "create", "--name", "scout", "--card", "not-a-real-card", "--model", "ollama/hermes-3"],
    )
    assert result.exit_code != 0
