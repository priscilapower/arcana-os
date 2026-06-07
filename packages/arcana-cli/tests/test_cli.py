"""Tests for arcana-cli commands — basic invocation and output sanity."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    for cmd in ("agent", "run", "init", "status", "eval"):
        assert cmd in result.output


# ---------------------------------------------------------------------------
# init / status
# ---------------------------------------------------------------------------


def test_init_creates_arcana_home(tmp_path, monkeypatch):
    import arcana_cli.commands.run as run_mod

    fake_home = tmp_path / ".arcana"
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert fake_home.is_dir()
    assert (fake_home / "agents").is_dir()
    assert (fake_home / "config.json").is_file()


def test_init_already_exists_is_noop(tmp_path, monkeypatch):
    import arcana_cli.commands.run as run_mod

    fake_home = tmp_path / ".arcana"
    fake_home.mkdir()
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "already exists" in result.output


def test_status_without_init_exits_nonzero(tmp_path, monkeypatch):
    import arcana_cli.commands.run as run_mod

    monkeypatch.setattr(run_mod, "ARCANA_HOME", tmp_path / ".arcana")
    result = runner.invoke(app, ["status"])
    assert result.exit_code != 0


def test_status_with_init_exits_zero(tmp_path, monkeypatch):
    import arcana_cli.commands.run as run_mod

    fake_home = tmp_path / ".arcana"
    fake_home.mkdir()
    (fake_home / "agents").mkdir()
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_routes_via_world():
    result = runner.invoke(app, ["run", "hello"])
    assert result.exit_code == 0
    assert "World" in result.output


def test_run_with_named_agent():
    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0
    assert "scout" in result.output


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------


def test_eval_list_all_cases():
    result = runner.invoke(app, ["eval", "list"])
    assert result.exit_code == 0


def test_eval_list_cards_suite():
    result = runner.invoke(app, ["eval", "list", "--suite", "cards"])
    assert result.exit_code == 0


def test_eval_list_blending_suite():
    result = runner.invoke(app, ["eval", "list", "--suite", "blending"])
    assert result.exit_code == 0
