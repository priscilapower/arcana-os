"""Tests for arcana providers show / edit / remove and ConnectionStore internals."""

from unittest.mock import patch

from typer.testing import CliRunner

import arcana_cli.commands.providers as providers_mod
from arcana.models import ConnectionStore
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_connection(path, *, name: str, provider: str, model_id: str, endpoint: str = "") -> ModelConnection:
    conn = ModelConnection(
        name=name,
        provider=ModelProvider(provider),
        default_model=model_id,
        endpoint=endpoint,
    )
    store = ConnectionStore(path)
    store.upsert(conn)
    return store.get_by_name(name)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# providers show
# ---------------------------------------------------------------------------


def test_providers_show_known(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(tmp_path / "models.json", name="my-ollama", provider="ollama", model_id="hermes-3")
    result = runner.invoke(app, ["providers", "show", "my-ollama"])
    assert result.exit_code == 0
    assert "my-ollama" in result.output
    assert "ollama" in result.output
    assert "hermes-3" in result.output


def test_providers_show_unknown_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    result = runner.invoke(app, ["providers", "show", "nonexistent"])
    assert result.exit_code != 0
    assert "No connection found" in result.output


def test_providers_show_no_secret_in_output(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(
        tmp_path / "models.json", name="my-anthropic", provider="anthropic", model_id="claude-sonnet-4-6"
    )

    fake_key = "sk-super-secret-key"
    with patch("keyring.get_password", return_value=fake_key):
        result = runner.invoke(app, ["providers", "show", "my-anthropic"])

    assert result.exit_code == 0
    assert fake_key not in result.output
    assert "keyring" in result.output


# ---------------------------------------------------------------------------
# providers edit
# ---------------------------------------------------------------------------


def test_providers_edit_base_url(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(
        tmp_path / "models.json",
        name="my-ollama",
        provider="ollama",
        model_id="hermes-3",
        endpoint="http://localhost:11434",
    )
    result = runner.invoke(
        app,
        ["providers", "edit", "my-ollama", "--base-url", "http://gpu-box:11434", "--no-verify"],
    )
    assert result.exit_code == 0
    updated = ConnectionStore(tmp_path / "models.json").get_by_name("my-ollama")
    assert updated is not None
    assert updated.endpoint == "http://gpu-box:11434"


def test_providers_edit_rotate_key_via_env(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(
        tmp_path / "models.json",
        name="my-anthropic",
        provider="anthropic",
        model_id="claude-sonnet-4-6",
    )
    monkeypatch.setenv("NEW_ANTHROPIC_KEY", "sk-new-key-from-env")
    keyring_store: dict[str, str] = {}

    with patch("keyring.set_password", side_effect=lambda s, k, v: keyring_store.update({k: v})):
        result = runner.invoke(
            app,
            ["providers", "edit", "my-anthropic", "--api-key-env", "NEW_ANTHROPIC_KEY", "--no-verify"],
        )

    assert result.exit_code == 0, result.output
    assert "sk-new-key-from-env" not in result.output
    assert any("api_key" in k for k in keyring_store)


def test_providers_edit_rotate_key_interactive(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(
        tmp_path / "models.json",
        name="my-openai",
        provider="openai",
        model_id="gpt-4",
    )
    keyring_store: dict[str, str] = {}

    with patch("keyring.set_password", side_effect=lambda s, k, v: keyring_store.update({k: v})):
        result = runner.invoke(
            app,
            ["providers", "edit", "my-openai", "--rotate-key", "--no-verify"],
            input="sk-rotated-key\n",
        )

    assert result.exit_code == 0, result.output
    assert "sk-rotated-key" not in result.output
    assert any("api_key" in k for k in keyring_store)


def test_providers_edit_credential_on_ollama_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(tmp_path / "models.json", name="my-ollama", provider="ollama", model_id="hermes-3")
    result = runner.invoke(app, ["providers", "edit", "my-ollama", "--rotate-key"])
    assert result.exit_code != 0
    assert "does not use a credential" in result.output


def test_providers_edit_custom_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(
        tmp_path / "models.json",
        name="my-custom",
        provider="custom",
        model_id="my-model",
        endpoint="https://proxy.internal/v1",
    )
    result = runner.invoke(
        app,
        [
            "providers",
            "edit",
            "my-custom",
            "--header",
            "X-Org: acme",
            "--header",
            "X-Env: prod",
            "--no-verify",
        ],
    )
    assert result.exit_code == 0, result.output
    updated = ConnectionStore(tmp_path / "models.json").get_by_name("my-custom")
    assert updated is not None
    assert updated.headers == {"X-Org": "acme", "X-Env": "prod"}


def test_providers_edit_headers_on_non_custom_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(tmp_path / "models.json", name="my-ollama", provider="ollama", model_id="hermes-3")
    result = runner.invoke(app, ["providers", "edit", "my-ollama", "--header", "X-Foo: bar", "--no-verify"])
    assert result.exit_code != 0


def test_providers_edit_unknown_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    result = runner.invoke(app, ["providers", "edit", "nonexistent", "--base-url", "http://x", "--no-verify"])
    assert result.exit_code != 0


def test_providers_edit_updates_updated_at(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    conn = _write_connection(
        tmp_path / "models.json",
        name="my-ollama",
        provider="ollama",
        model_id="hermes-3",
        endpoint="http://localhost:11434",
    )
    original_updated_at = conn.updated_at
    runner.invoke(
        app,
        ["providers", "edit", "my-ollama", "--base-url", "http://new-host:11434", "--no-verify"],
    )
    updated = ConnectionStore(tmp_path / "models.json").get_by_name("my-ollama")
    assert updated is not None
    assert updated.updated_at >= original_updated_at


def test_providers_edit_interactive_blank_keeps_value(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(
        tmp_path / "models.json",
        name="my-ollama",
        provider="ollama",
        model_id="hermes-3",
        endpoint="http://localhost:11434",
    )
    result = runner.invoke(
        app,
        ["providers", "edit", "my-ollama", "--no-verify"],
        input="\n",  # blank endpoint prompt keeps current value
    )
    assert result.exit_code == 0, result.output
    updated = ConnectionStore(tmp_path / "models.json").get_by_name("my-ollama")
    assert updated is not None
    assert updated.endpoint == "http://localhost:11434"


# ---------------------------------------------------------------------------
# providers remove
# ---------------------------------------------------------------------------


def test_providers_remove_simple(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(providers_mod, "AGENTS_BASE", tmp_path / "agents")
    _write_connection(tmp_path / "models.json", name="my-ollama", provider="ollama", model_id="hermes-3")

    with patch("keyring.delete_password"):
        result = runner.invoke(app, ["providers", "remove", "my-ollama", "--yes"])

    assert result.exit_code == 0
    assert ConnectionStore(tmp_path / "models.json").get_by_name("my-ollama") is None


def test_providers_remove_cleans_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(providers_mod, "AGENTS_BASE", tmp_path / "agents")
    _write_connection(
        tmp_path / "models.json", name="my-anthropic", provider="anthropic", model_id="claude-sonnet-4-6"
    )
    deleted_keys: list[str] = []

    with patch("keyring.delete_password", side_effect=lambda s, k: deleted_keys.append(k)):
        runner.invoke(app, ["providers", "remove", "my-anthropic", "--yes"])

    assert any("api_key" in k for k in deleted_keys)


def test_providers_remove_aborts_with_dependents(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(providers_mod, "AGENTS_BASE", tmp_path / "agents")

    _write_connection(
        tmp_path / "models.json", name="my-anthropic", provider="anthropic", model_id="claude-sonnet-4-6"
    )

    from arcana.agents.registry import AgentRegistry
    from arcana.types.agent import Agent as AgentRecord
    from arcana.types.card import Card

    registry = AgentRegistry(tmp_path / "agents")
    registry.save(
        AgentRecord(
            name="my-agent",
            card=Card.FOOL,
            model="anthropic:my-anthropic/claude-sonnet-4-6",
            system_prompt="test",
            temperature=0.7,
        )
    )

    result = runner.invoke(app, ["providers", "remove", "my-anthropic", "--yes"])
    assert result.exit_code != 0
    assert "my-agent" in result.output
    assert ConnectionStore(tmp_path / "models.json").get_by_name("my-anthropic") is not None


def test_providers_remove_force_with_dependents(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(providers_mod, "AGENTS_BASE", tmp_path / "agents")

    _write_connection(
        tmp_path / "models.json", name="my-anthropic", provider="anthropic", model_id="claude-sonnet-4-6"
    )

    from arcana.agents.registry import AgentRegistry
    from arcana.types.agent import Agent as AgentRecord
    from arcana.types.card import Card

    registry = AgentRegistry(tmp_path / "agents")
    registry.save(
        AgentRecord(
            name="my-agent",
            card=Card.FOOL,
            model="anthropic:my-anthropic/claude-sonnet-4-6",
            system_prompt="test",
            temperature=0.7,
        )
    )

    with patch("keyring.delete_password"):
        result = runner.invoke(app, ["providers", "remove", "my-anthropic", "--yes", "--force"])

    assert result.exit_code == 0
    assert ConnectionStore(tmp_path / "models.json").get_by_name("my-anthropic") is None
    assert "credential error" in result.output or "fail" in result.output


def test_providers_remove_unknown_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(providers_mod, "AGENTS_BASE", tmp_path / "agents")
    result = runner.invoke(app, ["providers", "remove", "nonexistent", "--yes"])
    assert result.exit_code != 0


def test_providers_remove_warns_default_model(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    monkeypatch.setattr(providers_mod, "AGENTS_BASE", tmp_path / "agents")

    _write_connection(
        tmp_path / "models.json", name="my-anthropic", provider="anthropic", model_id="claude-sonnet-4-6"
    )

    warned: list[str] = []

    def fake_warn(provider: str) -> None:
        warned.append(provider)

    monkeypatch.setattr(providers_mod, "_warn_default_model", fake_warn)

    with patch("keyring.delete_password"):
        result = runner.invoke(app, ["providers", "remove", "my-anthropic", "--yes"])

    assert result.exit_code == 0
    assert "anthropic" in warned


# ---------------------------------------------------------------------------
# ConnectionStore unit tests
# ---------------------------------------------------------------------------


def test_connection_store_upsert_updates_updated_at(tmp_path):
    path = tmp_path / "models.json"
    conn = ModelConnection(name="test", provider=ModelProvider.OLLAMA, default_model="hermes-3")
    store = ConnectionStore(path)
    store.upsert(conn)
    first = store.get_by_name("test")
    assert first is not None

    store.upsert(first.model_copy(update={"endpoint": "http://new:11434"}))
    second = store.get_by_name("test")
    assert second is not None
    assert second.updated_at >= first.updated_at


def test_connection_store_delete_removes_record(tmp_path):
    path = tmp_path / "models.json"
    store = ConnectionStore(path)
    store.upsert(ModelConnection(name="bye", provider=ModelProvider.OLLAMA, default_model="m"))
    assert store.get_by_name("bye") is not None

    with patch("keyring.delete_password"):
        store.delete("bye")

    assert store.get_by_name("bye") is None


def test_connection_store_atomic_write_creates_no_partial_file(tmp_path):
    path = tmp_path / "models.json"
    store = ConnectionStore(path)
    store.upsert(ModelConnection(name="a", provider=ModelProvider.OLLAMA, default_model="m"))
    tmp_files = list(tmp_path.glob(".models_*.tmp"))
    assert tmp_files == []


def test_connection_store_credential_set_and_delete(tmp_path):
    path = tmp_path / "models.json"
    store = ConnectionStore(path)
    written: dict[str, str] = {}
    deleted: list[str] = []

    with (
        patch("keyring.set_password", side_effect=lambda s, k, v: written.update({k: v})),
        patch("keyring.delete_password", side_effect=lambda s, k: deleted.append(k)),
    ):
        store.set_credential("my-ref", "super-secret")
        store.delete_credential("my-ref")

    assert written.get("my-ref") == "super-secret"
    assert "my-ref" in deleted


def test_no_secret_in_edit_output(tmp_path, monkeypatch):
    """Secrets must never appear in stdout or stderr across the edit path."""
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", tmp_path / "models.json")
    _write_connection(tmp_path / "models.json", name="my-openai", provider="openai", model_id="gpt-4")
    secret = "sk-top-secret-value-xyz"
    monkeypatch.setenv("MY_KEY", secret)
    keyring_store: dict[str, str] = {}

    with patch("keyring.set_password", side_effect=lambda s, k, v: keyring_store.update({k: v})):
        result = runner.invoke(
            app,
            ["providers", "edit", "my-openai", "--api-key-env", "MY_KEY", "--no-verify"],
        )

    assert result.exit_code == 0, result.output
    assert secret not in result.output
    assert secret not in (result.stderr or "")
