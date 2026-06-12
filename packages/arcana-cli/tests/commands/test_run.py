"""Tests for arcana init, status, and run commands."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.run as run_mod
from arcana.agents.registry import AgentRegistry
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    home = tmp_path / ".arcana"
    home.mkdir()
    (home / "agents").mkdir()
    (home / "connections").mkdir()
    monkeypatch.setattr(run_mod, "ARCANA_HOME", home)
    return home


@pytest.fixture()
def conn_fixture(arcana_home):
    conn = ModelConnection(
        name="ollama/hermes-3",
        provider=ModelProvider.OLLAMA,
        model_id="hermes-3",
        endpoint="http://localhost:11434",
    )
    path = arcana_home / "connections" / "models.json"
    path.write_text(json.dumps([json.loads(conn.model_dump_json())]))
    return conn


@pytest.fixture()
def agent_fixture(arcana_home, conn_fixture):
    reg = AgentRegistry(arcana_home / "agents")
    return reg.create(name="scout", card=Card.HERMIT, model_connection_id=conn_fixture.id)


# ---------------------------------------------------------------------------
# arcana init
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# arcana status
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# arcana run — World routing (no --agent)
# ---------------------------------------------------------------------------


def test_run_with_empty_prompt_exits_nonzero():
    result = runner.invoke(app, ["run", "", "--agent", "scout"])
    assert result.exit_code != 0
    assert "empty" in result.output


def test_run_without_agent_exits_nonzero():
    result = runner.invoke(app, ["run", "hello"])
    assert result.exit_code != 0
    assert "--agent" in result.output


# ---------------------------------------------------------------------------
# arcana run --agent — error paths
# ---------------------------------------------------------------------------


def test_run_with_agent_not_found(arcana_home):
    result = runner.invoke(app, ["run", "hello", "--agent", "ghost"])
    assert result.exit_code != 0
    assert "No agent" in result.output


def test_run_with_agent_no_connection(arcana_home):
    """Agent record exists but its connection was deleted from the store."""
    from uuid import uuid4

    reg = AgentRegistry(arcana_home / "agents")
    reg.create(name="orphan", card=Card.HERMIT, model_connection_id=uuid4())
    result = runner.invoke(app, ["run", "hello", "--agent", "orphan"])
    assert result.exit_code != 0
    assert "connection" in result.output.lower()


# ---------------------------------------------------------------------------
# arcana run --agent — success paths (gateway mocked)
# ---------------------------------------------------------------------------


class _MockGateway:
    """Minimal async context manager stand-in for ModelGateway."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_MockGateway":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


def test_run_with_agent_success(agent_fixture, arcana_home, monkeypatch):
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="The hermit speaks.")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0, result.output
    assert "The hermit speaks." in result.output
    assert "scout" in result.output


def test_run_with_agent_stream(agent_fixture, arcana_home, monkeypatch):
    async def _fake_stream(prompt: str, context: str | None = None):
        for chunk in ["Hello", " from", " stream"]:
            yield chunk

    mock_runtime = MagicMock()
    mock_runtime.stream = _fake_stream

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--stream"])
    assert result.exit_code == 0, result.output
    assert "Hello from stream" in result.output


def test_run_with_agent_by_uuid(agent_fixture, arcana_home, monkeypatch):
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="UUID lookup works.")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", str(agent_fixture.id)])
    assert result.exit_code == 0, result.output
    assert "UUID lookup works." in result.output
