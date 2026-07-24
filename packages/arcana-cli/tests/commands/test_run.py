"""Tests for arcana init, status, and run commands."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.run as run_mod
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory.federation import MemoryFederation
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana.types.session import MessageRole
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
        default_model="hermes-3",
        endpoint="http://localhost:11434",
    )
    path = arcana_home / "connections" / "models.json"
    path.write_text(json.dumps([json.loads(conn.model_dump_json())]))
    return conn


@pytest.fixture()
def agent_fixture(arcana_home, conn_fixture):
    reg = AgentRegistry(arcana_home / "agents")
    return reg.create(name="scout", card=Card.HERMIT, model="ollama/hermes-3")


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


def test_init_writes_memory_config_block(tmp_path, monkeypatch):
    fake_home = tmp_path / ".arcana"
    monkeypatch.setattr(run_mod, "ARCANA_HOME", fake_home)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    config = json.loads((fake_home / "config.json").read_text())
    assert config["memory"] == {
        "enabled": True,
        "private": "sqlite",
        "global": "vector",
        "pools": [],
        "extraction": {
            "strategy": "heuristic",
            "agent_confidence_cap": 0.7,
            "summarise_on_close": True,
            "min_confidence_to_store": 0.3,
        },
    }
    assert (fake_home / "vector").is_dir()


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


def test_run_with_agent_no_model(arcana_home):
    """Agent record exists but has no model configured."""
    reg = AgentRegistry(arcana_home / "agents")
    reg.create(name="orphan", card=Card.HERMIT, model="")
    result = runner.invoke(app, ["run", "hello", "--agent", "orphan"])
    assert result.exit_code != 0
    assert "model" in result.output.lower()


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
    async def _fake_stream(prompt: str, *, session=None, context: str | None = None):
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


# ---------------------------------------------------------------------------
# arcana run — memory injection + teardown
# ---------------------------------------------------------------------------


def _memory_db(arcana_home, agent_id) -> "object":
    return arcana_home / "agents" / str(agent_id) / "memory.db"


def test_run_injects_memory_and_tears_it_down(agent_fixture, arcana_home, monkeypatch):
    """Default run assembles a private federation and closes it after the turn."""
    closed: list[bool] = []
    original_aclose = MemoryFederation.aclose

    async def _spy_aclose(self) -> None:
        closed.append(True)
        await original_aclose(self)

    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="remembered")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(run_mod, "resolve_embedding_gateway", lambda: None)  # SQLite-only, deterministic
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)
    monkeypatch.setattr(MemoryFederation, "aclose", _spy_aclose)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0, result.output
    # Private store was materialised, then the federation was closed with the run.
    assert _memory_db(arcana_home, agent_fixture.id).exists()
    assert closed == [True]


def test_run_no_memory_is_stateless(agent_fixture, arcana_home, monkeypatch):
    """--no-memory skips assembly entirely — no per-agent store is created."""
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="stateless")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--no-memory"])
    assert result.exit_code == 0, result.output
    assert "stateless" in result.output
    assert not _memory_db(arcana_home, agent_fixture.id).exists()


def test_run_respects_memory_disabled_in_config(agent_fixture, arcana_home, monkeypatch):
    """A config.json memory block with enabled=false also runs stateless."""
    (arcana_home / "config.json").write_text(json.dumps({"memory": {"enabled": False}}))
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="off")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0, result.output
    assert not _memory_db(arcana_home, agent_fixture.id).exists()


# ---------------------------------------------------------------------------
# arcana run — session footer
# ---------------------------------------------------------------------------


def test_run_prints_session_id_footer(agent_fixture, arcana_home, monkeypatch):
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="response")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0, result.output
    assert "session:" in result.output
    assert "--session" in result.output
    assert "--continue" in result.output


# ---------------------------------------------------------------------------
# arcana run --session / --continue flags
# ---------------------------------------------------------------------------


def test_run_session_and_continue_mutually_exclusive(agent_fixture, arcana_home):
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--session", "abc", "--continue"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_run_session_unknown_id_exits_nonzero(agent_fixture, arcana_home, monkeypatch):
    from uuid import uuid4

    unknown_id = str(uuid4())
    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: MagicMock())

    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--session", unknown_id])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_run_session_invalid_uuid_exits_nonzero(agent_fixture, arcana_home, monkeypatch):
    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--session", "not-a-uuid"])
    assert result.exit_code != 0
    assert "Invalid session id" in result.output


def test_run_continue_with_no_prior_sessions_starts_new(agent_fixture, arcana_home, monkeypatch):
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="fresh start")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--continue"])
    assert result.exit_code == 0, result.output
    assert "No prior sessions" in result.output


def test_run_session_roundtrip(agent_fixture, arcana_home, monkeypatch):
    """session id printed in footer round-trips into --session for a successful resume."""
    sm = SessionManager(arcana_home / "agents")

    # Pre-create a persisted session for the agent
    session = sm.start(agent_fixture.id)
    session.add_message(MessageRole.USER, "prior turn")
    session.add_message(MessageRole.ASSISTANT, "prior answer")
    sm.close(session)
    prior_id = str(session.id)

    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="resumed")

    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *args, **kwargs: mock_runtime)

    # Resume the session by id
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--session", prior_id])
    assert result.exit_code == 0, result.output
    assert "resumed" in result.output
