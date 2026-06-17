"""Tests for arcana connect commands."""

from unittest.mock import patch

from typer.testing import CliRunner

import arcana_cli.commands.connect as connect_mod
from arcana.models import ConnectionStore
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


def test_connect_model_overwrite_preserves_id_and_keys_to_same_id(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    keyring_store: dict[str, str] = {}

    def fake_set_password(service, key, value):
        keyring_store[key] = value

    with patch("keyring.set_password", side_effect=fake_set_password):
        runner.invoke(
            app,
            [
                "connect",
                "model",
                "--provider",
                "anthropic",
                "--model-id",
                "claude-sonnet-4-6",
                "--name",
                "my-anthropic",
                "--api-key",
                "key-v1",
            ],
            input="y\n",
        )
        first_id = ConnectionStore(tmp_path / "models.json").get_by_name("my-anthropic").id

        runner.invoke(
            app,
            [
                "connect",
                "model",
                "--provider",
                "anthropic",
                "--model-id",
                "claude-opus-4-8",
                "--name",
                "my-anthropic",
                "--api-key",
                "key-v2",
            ],
            input="y\n",
        )

    store = ConnectionStore(tmp_path / "models.json")
    connections = store.all()
    assert len(connections) == 1
    updated = connections[0]
    assert updated.id == first_id, "ID must be preserved on overwrite"
    assert updated.model_id == "claude-opus-4-8"
    assert f"{first_id}_api_key" in keyring_store, "API key must be stored under the original ID"
    assert keyring_store[f"{first_id}_api_key"] == "key-v2"


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
