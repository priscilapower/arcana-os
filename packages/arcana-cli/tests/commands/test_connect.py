"""Tests for arcana connect commands."""

from typer.testing import CliRunner

import arcana_cli.commands.connect as connect_mod
from arcana_cli.main import app

runner = CliRunner()


def test_connect_list_no_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    result = runner.invoke(app, ["connect", "list"])
    assert result.exit_code == 0
    assert "No connections" in result.output


def test_connect_model_with_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    result = runner.invoke(
        app,
        [
            "connect",
            "model",
            "--provider",
            "ollama",
            "--model-id",
            "hermes-3",
            "--name",
            "local-hermes",
            "--endpoint",
            "http://localhost:11434",
        ],
    )
    assert result.exit_code == 0
    assert "local-hermes" in result.output
    assert (tmp_path / "models.json").exists()


def test_connect_model_unknown_provider_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    result = runner.invoke(
        app,
        [
            "connect",
            "model",
            "--provider",
            "unknown-provider",
            "--model-id",
            "some-model",
            "--name",
            "test",
        ],
    )
    assert result.exit_code != 0


def test_connect_list_shows_saved_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    runner.invoke(
        app,
        [
            "connect",
            "model",
            "--provider",
            "ollama",
            "--model-id",
            "hermes-3",
            "--name",
            "my-local",
            "--endpoint",
            "http://localhost:11434",
        ],
    )
    result = runner.invoke(app, ["connect", "list"])
    assert result.exit_code == 0
    assert "my-local" in result.output
