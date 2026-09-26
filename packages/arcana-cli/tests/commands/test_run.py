"""Tests for arcana init, status, and run commands."""

import asyncio
import io
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console
from typer.testing import CliRunner

import arcana_cli.commands.run as run_mod
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory.federation import MemoryFederation
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana.types.session import MessageRole
from arcana_cli._render import EXIT_ERROR
from arcana_cli.commands.run import RunError, init_home, run_turn, show_status
from arcana_cli.main import app
from arcana_cli.ui.renderer import TtyRenderer
from tests.support.renderer import RecordingRenderer

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


def test_run_without_agent_and_no_agents_asks_for_agent(arcana_home):
    # No agents configured → the World can't pick one → asks the user to name one.
    result = runner.invoke(app, ["run", "hello"])
    assert result.exit_code != 0
    assert "--agent" in result.output


def test_run_without_agent_routes_to_sole_agent(agent_fixture, arcana_home, monkeypatch):
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="routed reply")
    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)

    result = runner.invoke(app, ["run", "hello"])  # no --agent
    assert result.exit_code == 0, result.output
    assert "routed reply" in result.output
    assert "The World routed to" in result.output
    assert "scout" in result.output


def test_run_without_agent_ambiguous_asks_for_agent(arcana_home, conn_fixture):
    reg = AgentRegistry(arcana_home / "agents")
    reg.create(name="one", card=Card.HERMIT, model="ollama/hermes-3")
    reg.create(name="two", card=Card.HERMIT, model="ollama/hermes-3")
    result = runner.invoke(app, ["run", "hello"])  # two candidates, no rule/default
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


def test_run_with_agent_writes_explicit_routing_audit(agent_fixture, arcana_home, monkeypatch):
    """--agent bypasses routing and records an EXPLICIT decision to the audit."""
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="ok")
    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)

    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0, result.output
    audit = arcana_home / "world" / "routing_audit.jsonl"
    assert audit.exists()
    assert '"layer":"explicit"' in audit.read_text()


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


# ---------------------------------------------------------------------------
# arcana run — golden output (what a terminal shows is unchanged)
# ---------------------------------------------------------------------------

GOLDEN = Path(__file__).parent / "golden" / "run"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHORT_ID = re.compile(r"session: [0-9a-f]{8}")


def _normalised(output: str) -> str:
    """Session ids are fresh per run; replace them so the golden is stable."""
    return _SHORT_ID.sub("session: <short-id>", _UUID.sub("<session-id>", output))


async def _three_chunks(prompt: str, *, session=None, context: str | None = None):
    for chunk in ["Hello", " from", " stream"]:
        yield chunk


@pytest.fixture()
def terminal(monkeypatch) -> io.StringIO:
    """One screen that stdout and stderr both land on, in the order they're written — as a terminal shows them."""
    screen = io.StringIO()

    def _renderer(json: bool) -> TtyRenderer:
        assert not json
        return TtyRenderer(Console(file=screen, width=100), stderr=Console(file=screen, width=100))

    monkeypatch.setattr(run_mod, "renderer_for", _renderer)
    return screen


@pytest.mark.parametrize(
    ("golden", "args"),
    [
        ("agent_panel", ["run", "hello", "--agent", "scout"]),
        ("agent_stream", ["run", "hello", "--agent", "scout", "--stream"]),
        ("routed_panel", ["run", "hello"]),
        ("no_agent", ["run", "hello", "--agent", "ghost"]),
    ],
)
def test_run_terminal_output_matches_golden(
    agent_fixture, arcana_home, monkeypatch, terminal: io.StringIO, golden: str, args: list[str]
):
    # The goldens were recorded from the command before it took a renderer.
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value="The hermit speaks.")
    mock_runtime.stream = _three_chunks
    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)

    result = runner.invoke(app, args)
    expected = (GOLDEN / f"{golden}.txt").read_text()
    assert f"exit={result.exit_code}\n{_normalised(terminal.getvalue())}" == expected


# ---------------------------------------------------------------------------
# arcana run — stdout carries the reply alone
# ---------------------------------------------------------------------------


def _stub_runtime(monkeypatch, *, reply: str = "The hermit speaks.", stream=_three_chunks) -> MagicMock:
    mock_runtime = MagicMock()
    mock_runtime.run = AsyncMock(return_value=reply)
    mock_runtime.stream = stream
    monkeypatch.setattr(run_mod, "ModelGateway", _MockGateway)
    monkeypatch.setattr(AgentRegistry, "build_runtime", lambda *a, **k: mock_runtime)
    return mock_runtime


def test_run_stream_piped_stdout_is_the_tokens_alone(agent_fixture, arcana_home, monkeypatch):
    _stub_runtime(monkeypatch)
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--stream"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "Hello from stream\n"
    assert "Agent: scout" in result.stderr
    assert "session:" in result.stderr


def test_run_panel_goes_to_stdout_and_notes_to_stderr(agent_fixture, arcana_home, monkeypatch):
    _stub_runtime(monkeypatch)
    result = runner.invoke(app, ["run", "hello", "--agent", "scout"])
    assert result.exit_code == 0, result.output
    assert "The hermit speaks." in result.stdout
    assert "Agent:" not in result.stdout
    assert "session:" not in result.stdout


# ---------------------------------------------------------------------------
# arcana run --json
# ---------------------------------------------------------------------------


def test_run_json_is_one_document(agent_fixture, arcana_home, monkeypatch):
    _stub_runtime(monkeypatch)
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["agent"] == "scout"
    assert doc["response"] == "The hermit speaks."
    assert _UUID.fullmatch(doc["session_id"])
    assert "usage" not in doc  # the stub model reported no token counts


def test_run_json_reports_usage_when_the_model_counts_tokens(agent_fixture, arcana_home, monkeypatch):
    runtime = _stub_runtime(monkeypatch)

    async def _counted(prompt: str, *, session=None, context: str | None = None) -> str:
        session.total_input_tokens = 12
        session.total_output_tokens = 34
        return "counted"

    runtime.run = _counted
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["usage"] == {"input_tokens": 12, "output_tokens": 34}


def test_run_json_routed_notes_stay_off_stdout(agent_fixture, arcana_home, monkeypatch):
    _stub_runtime(monkeypatch)
    result = runner.invoke(app, ["run", "hello", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["agent"] == "scout"
    assert "The World routed to scout" in result.stderr


def test_run_json_error_is_an_error_document(arcana_home):
    result = runner.invoke(app, ["run", "hello", "--agent", "ghost", "--json"])
    assert result.exit_code == EXIT_ERROR
    assert json.loads(result.stdout) == {"error": {"code": EXIT_ERROR, "message": "No agent 'ghost'."}}


def test_run_json_wraps_a_model_failure(agent_fixture, arcana_home, monkeypatch):
    runtime = _stub_runtime(monkeypatch)
    runtime.run = AsyncMock(side_effect=RuntimeError("model down"))
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--json"])
    assert result.exit_code == EXIT_ERROR
    assert json.loads(result.stdout)["error"]["message"] == "Error: model down"


def test_run_json_with_stream_is_a_usage_error(agent_fixture, arcana_home):
    result = runner.invoke(app, ["run", "hello", "--agent", "scout", "--json", "--stream"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--stream" in result.stderr


def test_run_ambiguous_agent_name_lists_the_ids(arcana_home, conn_fixture):
    reg = AgentRegistry(arcana_home / "agents")
    first = reg.create(name="twin", card=Card.HERMIT, model="ollama/hermes-3")
    second = reg.create(name="twin", card=Card.HERMIT, model="ollama/hermes-3")
    result = runner.invoke(app, ["run", "hello", "--agent", "twin", "--json"])
    assert result.exit_code == EXIT_ERROR
    message = json.loads(result.stdout)["error"]["message"]
    assert "Ambiguous agent name 'twin'" in message
    assert str(first.id) in message and str(second.id) in message


# ---------------------------------------------------------------------------
# run_turn — the renderer-agnostic body
# ---------------------------------------------------------------------------


async def test_run_turn_stream_stops_the_status_before_the_first_chunk(agent_fixture, arcana_home, monkeypatch):
    _stub_runtime(monkeypatch)
    r = RecordingRenderer()
    result = await run_turn(r, "hello", agent="scout", stream=True, no_memory=True)
    (thinking,) = r.statuses
    assert "thinking" in thinking
    assert r.events == [
        ("status", thinking),
        ("stop", thinking),
        ("chunk", "Hello"),
        ("chunk", " from"),
        ("chunk", " stream"),
    ]
    assert result.response == "Hello from stream"
    assert r.emitted == []  # the reply was streamed, not emitted
    assert "Agent: scout" in r.notes_text()


async def test_run_turn_stream_with_no_chunks_still_ends_the_status(agent_fixture, arcana_home, monkeypatch):
    async def _silent(prompt: str, *, session=None, context: str | None = None):
        return
        yield

    _stub_runtime(monkeypatch, stream=_silent)
    r = RecordingRenderer()
    result = await run_turn(r, "hello", agent="scout", stream=True, no_memory=True)
    assert [kind for kind, _ in r.events] == ["status", "stop"]
    assert result.response == ""


async def test_run_turn_without_stream_returns_the_reply_under_a_status(agent_fixture, arcana_home, monkeypatch):
    _stub_runtime(monkeypatch)
    r = RecordingRenderer()
    result = await run_turn(r, "hello", agent="scout", no_memory=True)
    assert result.agent == "scout"
    assert result.card is Card.HERMIT
    assert result.response == "The hermit speaks."
    assert [kind for kind, _ in r.events] == ["status", "stop"]
    assert r.streamed == []


async def test_run_turn_errors_raise_run_error(arcana_home):
    with pytest.raises(RunError, match="Prompt cannot be empty"):
        await run_turn(RecordingRenderer(), "  ", agent="scout")
    with pytest.raises(RunError, match="No agent 'ghost'"):
        await run_turn(RecordingRenderer(), "hello", agent="ghost")


def _spy_federation_close(monkeypatch) -> list[bool]:
    closed: list[bool] = []
    original_aclose = MemoryFederation.aclose

    async def _spy_aclose(self) -> None:
        closed.append(True)
        await original_aclose(self)

    monkeypatch.setattr(run_mod, "resolve_embedding_gateway", lambda: None)  # SQLite-only, deterministic
    monkeypatch.setattr(MemoryFederation, "aclose", _spy_aclose)
    return closed


async def test_run_turn_closes_the_federation_when_cancelled_mid_stream(agent_fixture, arcana_home, monkeypatch):
    async def _cancelled(prompt: str, *, session=None, context: str | None = None):
        yield "partial"
        raise asyncio.CancelledError

    _stub_runtime(monkeypatch, stream=_cancelled)
    closed = _spy_federation_close(monkeypatch)
    r = RecordingRenderer()
    with pytest.raises(asyncio.CancelledError):
        await run_turn(r, "hello", agent="scout", stream=True)
    assert closed == [True]
    assert r.streamed == ["partial"]
    assert r.events[-1] == ("chunk", "partial")


async def test_run_turn_closes_the_federation_when_the_stream_fails(agent_fixture, arcana_home, monkeypatch):
    async def _broken(prompt: str, *, session=None, context: str | None = None):
        yield "partial"
        raise RuntimeError("connection reset")

    _stub_runtime(monkeypatch, stream=_broken)
    closed = _spy_federation_close(monkeypatch)
    with pytest.raises(RunError, match="Error: connection reset"):
        await run_turn(RecordingRenderer(), "hello", agent="scout", stream=True)
    assert closed == [True]


# ---------------------------------------------------------------------------
# init / status — the renderer-agnostic bodies
# ---------------------------------------------------------------------------


async def test_init_home_writes_under_a_status_then_shows_the_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "ARCANA_HOME", tmp_path / ".arcana")
    r = RecordingRenderer()
    await init_home(r)
    assert r.statuses == [f"[bold {run_mod.GREEN}]Initialising Arcana OS...[/]"]
    assert "Arcana OS initialised." in r.text()
    assert (tmp_path / ".arcana" / "world.json").is_file()


async def test_show_status_counts_agents(agent_fixture, arcana_home):
    r = RecordingRenderer()
    await show_status(r)
    text = r.text()
    assert "Agents" in text and "1" in text
    assert "Connections" in text
