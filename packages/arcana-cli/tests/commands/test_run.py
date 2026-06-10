"""Tests for arcana init, status, and run commands."""

from typer.testing import CliRunner

import arcana_cli.commands.run as run_mod
from arcana_cli.main import app

runner = CliRunner()


def test_init_creates_arcana_home(tmp_path, monkeypatch):
    fake_home = tmp_path / ".arcana"
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert fake_home.is_dir()
    assert (fake_home / "agents").is_dir()
    assert (fake_home / "config.json").is_file()


def test_init_already_exists_is_noop(tmp_path, monkeypatch):
    fake_home = tmp_path / ".arcana"
    fake_home.mkdir()
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "already exists" in result.output


def test_status_without_init_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "ARCANA_HOME", tmp_path / ".arcana")
    result = runner.invoke(app, ["status"])
    assert result.exit_code != 0


def test_status_with_init_exits_zero(tmp_path, monkeypatch):
    fake_home = tmp_path / ".arcana"
    fake_home.mkdir()
    (fake_home / "agents").mkdir()
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


def test_run_routes_via_world():
    result = runner.invoke(app, ["run", "hello"])
    assert result.exit_code == 0
    assert "World" in result.output


def test_run_with_named_agent():
    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0
    assert "scout" in result.output
