"""Tests for the `arcana chat` interactive REPL.

The REPL is a full-screen prompt_toolkit app, but all of its behaviour lives in
:class:`chat._ChatController`, which is UI-independent. These tests drive the
controller directly — ``await controller.submit(line)`` — and assert on the
rendered transcript, so no terminal is needed. A handful of tests cover the
editor key bindings and the app wiring (accept handler, cancel) separately.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from prompt_toolkit import PromptSession
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner

import arcana_cli.commands.chat.app as chat_app
import arcana_cli.commands.chat.controller as chat_controller
import arcana_cli.commands.chat.editor as chat_editor
import arcana_cli.commands.run as run_mod
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana.types.session import MessageRole
from arcana_cli.commands.chat.app import _footer_fragments
from arcana_cli.commands.chat.controller import _ChatController
from arcana_cli.commands.chat.editor import (
    _agent_history,
    _build_key_bindings,
    _PasteRegistry,
    _should_collapse_paste,
    _SlashCompleter,
    _submits_on_enter,
    _trailing_backslashes,
)
from arcana_cli.commands.chat.render import _replay_blocks, _Transcript
from arcana_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    home = tmp_path / ".arcana"
    home.mkdir()
    (home / "agents").mkdir()
    (home / "connections").mkdir()
    # Each module reads ARCANA_HOME from its own namespace.
    monkeypatch.setattr(chat_app, "ARCANA_HOME", home)
    monkeypatch.setattr(chat_editor, "ARCANA_HOME", home)
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


class _MockGateway:
    """Minimal stand-in for ModelGateway — the controller only stores it."""

    def __init__(self, *args, **kwargs) -> None:
        pass


def _mock_runtime(chunks=("Hi",), *, received=None, error: Exception | None = None):
    """A stand-in runtime agent whose .stream yields *chunks* (or raises *error*)."""

    async def _fake_stream(prompt: str, *, session=None, context: str | None = None):
        if received is not None:
            received.append(prompt)
        if session is not None:
            session.add_message(MessageRole.USER, prompt)
        if error is not None:
            raise error
        for c in chunks:
            yield c
        if session is not None:
            session.add_message(MessageRole.ASSISTANT, "".join(chunks))

    rt = MagicMock()
    rt.name = "scout"  # real str — the reply eyebrow uppercases/escapes it
    rt.stream = _fake_stream
    rt.card_config = CardEngine(get_registry()).resolve(Card.HERMIT, [])
    return rt


def _make_controller(arcana_home, record, runtime, *, federation=None, memory_off=False, session=None):
    """Build a controller wired to *runtime*, with the header already appended."""
    reg = AgentRegistry(arcana_home / "agents")
    sm = SessionManager(arcana_home / "agents")
    sess = session if session is not None else sm.start(record.id)
    controller = _ChatController(
        reg=reg,
        gw=_MockGateway(),  # type: ignore[arg-type]
        sm=sm,
        record=record,
        session=sess,
        runtime_agent=runtime,
        federation=federation,
        memory_off=memory_off,
    )
    controller.append_header()
    return controller


async def _feed(controller, lines):
    """Submit a scripted sequence of lines, stopping if one exits the session."""
    for line in lines:
        await controller.submit(line)
        if controller.exited:
            break


def _patch_build(monkeypatch, runtime, federation=None):
    """Stub build_session_runtime so /fresh and /switch don't hit the model stack."""

    async def _fake(*args, **kwargs):
        return runtime, federation

    monkeypatch.setattr(chat_controller, "build_session_runtime", _fake)


# ---------------------------------------------------------------------------
# Validation / error paths (exit before the full-screen app is built)
# ---------------------------------------------------------------------------
def test_chat_without_agent_prints_world_deferral(arcana_home):
    result = runner.invoke(app, ["chat"])
    assert result.exit_code != 0
    assert "The World isn't available yet" in result.output
    assert "--agent" in result.output


def test_chat_agent_not_found(arcana_home):
    result = runner.invoke(app, ["chat", "--agent", "ghost"])
    assert result.exit_code != 0
    assert "No agent" in result.output


def test_chat_agent_no_model(arcana_home):
    reg = AgentRegistry(arcana_home / "agents")
    reg.create(name="orphan", card=Card.HERMIT, model="")
    result = runner.invoke(app, ["chat", "--agent", "orphan"])
    assert result.exit_code != 0
    assert "model" in result.output.lower()


def test_chat_session_invalid_uuid(agent_fixture, arcana_home):
    result = runner.invoke(app, ["chat", "--agent", "scout", "--session", "not-a-uuid"])
    assert result.exit_code != 0
    assert "Invalid session id" in result.output


def test_chat_session_unknown_id(agent_fixture, arcana_home):
    from uuid import uuid4

    result = runner.invoke(app, ["chat", "--agent", "scout", "--session", str(uuid4())])
    assert result.exit_code != 0
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# The turn loop (driven through the controller)
# ---------------------------------------------------------------------------
async def test_streams_a_turn(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(chunks=["Hello", " world"]))
    await _feed(c, ["hi", "/exit"])
    out = c.transcript.plain_text()
    assert "Hello world" in out  # streamed reply, rendered as markdown
    assert "scout" in out  # header shows the agent
    assert c.exited


async def test_header_shows_agent_card_and_memory(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    out = c.transcript.plain_text()
    assert "scout" in out
    assert "The Hermit" in out  # card slug title-cased
    assert "Memory" in out


async def test_labels_user_and_agent_turns(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(chunks=["hi back"]))
    await _feed(c, ["hello there"])
    out = c.transcript.plain_text()
    assert "YOU" in out  # ✦ YOU block for the sent message
    assert "SCOUT" in out  # ✦ SCOUT eyebrow above the reply
    assert "hello there" in out


async def test_turn_records_messages_on_session(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(chunks=["ok"]))
    await _feed(c, ["hi"])
    roles = [m.role for m in c.session.messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


async def test_exit_sets_flag(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/exit"])
    assert c.exited


async def test_multiline_message_is_passed_through(agent_fixture, arcana_home):
    received: list[str] = []
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(received=received))
    await _feed(c, ["line one\nline two"])
    assert received == ["line one\nline two"]


async def test_blank_line_is_ignored(agent_fixture, arcana_home):
    received: list[str] = []
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(received=received))
    await _feed(c, ["   ", "hi"])
    assert received == ["hi"]  # the whitespace-only line never reached the model


async def test_dims_think_block(agent_fixture, arcana_home):
    c = _make_controller(
        arcana_home,
        agent_fixture,
        _mock_runtime(chunks=["<think>planning the reply</think>", "The answer is 42."]),
    )
    await _feed(c, ["hi"])
    out = c.transcript.plain_text()
    assert "think>" not in out  # neither <think> nor </think> leak through
    assert "planning the reply" in out  # reasoning shown (dimmed)
    assert "The answer is 42." in out  # answer rendered as markdown


async def test_turn_error_does_not_crash_repl(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(error=RuntimeError("model down")))
    await _feed(c, ["hi"])
    assert "model down" in c.transcript.plain_text()


async def test_connection_error_shows_hint(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(error=ConnectionError("Connection refused")))
    await _feed(c, ["hi"])
    assert "provider running" in c.transcript.plain_text()


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
async def test_help_lists_commands(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/help"])
    out = c.transcript.plain_text()
    for token in ("/memory", "/card", "/switch", "/fresh", "/no-memory"):
        assert token in out


async def test_unknown_command_hints_help(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/bogus"])
    assert "Unknown command" in c.transcript.plain_text()


async def test_card_shows_config(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/card"])
    assert "Temperature" in c.transcript.plain_text()


async def test_no_memory_reports_off(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(), federation=None, memory_off=True)
    await _feed(c, ["/memory"])
    assert "off" in c.transcript.plain_text().lower()


async def test_fresh_starts_new_session_with_memory(agent_fixture, arcana_home, monkeypatch):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    _patch_build(monkeypatch, _mock_runtime(), federation=object())
    prior = c.session.id
    await _feed(c, ["/fresh"])
    out = c.transcript.plain_text()
    assert "new session" in out
    assert "memory on" in out
    assert c.session.id != prior  # a genuinely new session


async def test_no_memory_slash_starts_stateless_session(agent_fixture, arcana_home, monkeypatch):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    _patch_build(monkeypatch, _mock_runtime(), federation=None)
    await _feed(c, ["/no-memory"])
    out = c.transcript.plain_text()
    assert "new session" in out
    assert "memory off" in out
    assert c.memory_off is True


async def test_switch_unknown_agent(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/switch ghost"])
    assert "No agent 'ghost'" in c.transcript.plain_text()


async def test_switch_requires_name(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/switch"])
    assert "Usage: /switch" in c.transcript.plain_text()


async def test_switch_loads_named_agent(agent_fixture, arcana_home, monkeypatch):
    reg = AgentRegistry(arcana_home / "agents")
    reg.create(name="sage", card=Card.HIGH_PRIESTESS, model="ollama/hermes-3")
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    _patch_build(monkeypatch, _mock_runtime())
    await _feed(c, ["/switch sage"])
    out = c.transcript.plain_text()
    assert "sage" in out
    assert "deferred" in out.lower()
    assert c.record.name == "sage"


async def test_save_reports_snapshot(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/save"])
    assert "session saved" in c.transcript.plain_text()
    sessions_dir = arcana_home / "agents" / str(agent_fixture.id) / "sessions"
    assert list(sessions_dir.glob("*.json"))  # /save flushed a snapshot to disk


async def test_clear_keeps_header_only(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(chunks=["reply text"]))
    await _feed(c, ["hi"])
    assert "reply text" in c.transcript.plain_text()
    await _feed(c, ["/clear"])
    out = c.transcript.plain_text()
    assert "reply text" not in out  # transcript wiped
    assert "scout" in out  # header re-drawn


# ---------------------------------------------------------------------------
# /retry
# ---------------------------------------------------------------------------
async def test_retry_reruns_last_message(agent_fixture, arcana_home):
    received: list[str] = []
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(chunks=["ok"], received=received))
    await _feed(c, ["hello", "/retry"])
    assert received == ["hello", "hello"]  # sent once, then re-sent by /retry
    assert "retrying" in c.transcript.plain_text()


async def test_retry_with_no_history(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    await _feed(c, ["/retry"])
    assert "Nothing to retry" in c.transcript.plain_text()


# ---------------------------------------------------------------------------
# Resume replay
# ---------------------------------------------------------------------------
def _plain_blocks(blocks) -> str:
    t = _Transcript()
    for b in blocks:
        t.append(b)
    return t.plain_text()


def test_replay_renders_prior_messages():
    from arcana.types.session import Message

    session = MagicMock()
    session.messages = [
        Message(role=MessageRole.USER, content="what is a monad"),
        Message(role=MessageRole.ASSISTANT, content="A monad is a design pattern."),
    ]
    out = _plain_blocks(_replay_blocks(session, "scout", "#888888"))
    assert "Resuming" in out
    assert "what is a monad" in out
    assert "monad is a design pattern" in out


def test_replay_caps_and_notes_hidden():
    from arcana.types.session import Message

    session = MagicMock()
    session.messages = [
        Message(role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT, content=f"msg {i}") for i in range(12)
    ]
    out = _plain_blocks(_replay_blocks(session, "scout", "#888888"))
    assert "showing the last 8" in out  # 12 messages → last 8 shown
    assert "msg 11" in out  # newest is present
    assert "msg 0" not in out  # oldest is trimmed


def test_replay_empty_session_renders_nothing():
    session = MagicMock()
    session.messages = []
    assert _replay_blocks(session, "scout", "#888888") == []


# ---------------------------------------------------------------------------
# App wiring — accept handler, cancel, busy gating
# ---------------------------------------------------------------------------
async def test_accept_handler_runs_turn_and_clears(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(chunks=["done"]))
    buf = Buffer(multiline=True)
    buf.insert_text("hi there")
    keep = c.on_accept(buf)
    assert keep is False  # buffer is cleared after accept
    assert c._turn_task is not None
    await c._turn_task
    assert "done" in c.transcript.plain_text()


async def test_accept_handler_ignored_while_busy(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    c._turn_task = asyncio.ensure_future(asyncio.sleep(0.05))
    buf = Buffer(multiline=True)
    buf.insert_text("hi")
    assert c.on_accept(buf) is True  # kept, not submitted, while a turn is in flight
    await c._turn_task


async def test_cancel_turn_appends_cancelled_note(agent_fixture, arcana_home):
    started = asyncio.Event()

    async def _slow_stream(prompt, *, session=None, context=None):
        started.set()
        await asyncio.sleep(10)
        yield "never"

    rt = _mock_runtime()
    rt.stream = _slow_stream
    c = _make_controller(arcana_home, agent_fixture, rt)
    c.start_turn("hi")
    await started.wait()
    c.cancel_turn()
    await c._turn_task
    assert "cancelled" in c.transcript.plain_text().lower()


async def test_blank_start_turn_is_noop(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    c.start_turn("   ")
    assert c._turn_task is None  # nothing scheduled for whitespace


# ---------------------------------------------------------------------------
# Transcript rendering
# ---------------------------------------------------------------------------
def test_transcript_update_last_replaces_streaming_block():
    from rich.text import Text

    t = _Transcript()
    t.append(Text("first"))
    t.append(Text("partial"))
    t.update_last(Text("complete reply"))
    out = t.plain_text()
    assert "complete reply" in out
    assert "partial" not in out


def test_transcript_last_line_tracks_height():
    from rich.text import Text

    t = _Transcript()
    assert t.last_line == 0
    for i in range(5):
        t.append(Text(f"line {i}"))
    t.to_ansi(80)
    assert t.last_line >= 4  # the cursor anchors near the bottom of the content


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
def test_footer_shows_session_and_hints(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    text = "".join(frag for _, frag in _footer_fragments(c))
    assert f"#{str(c.session.id)[:4]}" in text
    assert "/help" in text
    assert "/exit" in text
    assert "Ctrl+C" in text


def test_footer_reports_memory_mode(agent_fixture, arcana_home):
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(), memory_off=True)
    text = "".join(frag for _, frag in _footer_fragments(c))
    assert "memory off" in text


# ---------------------------------------------------------------------------
# Input editor — backslash logic, key bindings, completion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "count"),
    [("", 0), ("hi", 0), ("hi\\", 1), ("hi\\\\", 2), ("a\\\\\\", 3)],
)
def test_trailing_backslashes(text, count):
    assert _trailing_backslashes(text) == count


@pytest.mark.parametrize(
    ("text", "submits"),
    [
        ("", True),
        ("hello", True),
        ("hello\\", False),  # one unescaped backslash → continuation
        ("hello\\\\", True),  # escaped pair → submit
        ("hello\\\\\\", False),  # odd → continuation
    ],
)
def test_submits_on_enter(text, submits):
    assert _submits_on_enter(text) is submits


async def _run_editor(keystrokes: str, pastes: _PasteRegistry | None = None) -> str:
    """Drive a real prompt_toolkit session headlessly and return the submitted text.

    Wrapped in a timeout so a key-binding regression that fails to submit surfaces
    as a fast failure instead of hanging the test run.
    """
    with create_pipe_input() as inp:
        inp.send_text(keystrokes)
        session = PromptSession(
            input=inp,
            output=DummyOutput(),
            key_bindings=_build_key_bindings(pastes or _PasteRegistry()),
            multiline=True,
        )
        return await asyncio.wait_for(session.prompt_async(), timeout=10)


def _bracketed(text: str) -> str:
    """Wrap *text* in the terminal's bracketed-paste escape sequence."""
    return f"\x1b[200~{text}\x1b[201~"


async def test_editor_enter_submits():
    assert await _run_editor("hello\r") == "hello"


async def test_editor_backslash_enter_inserts_newline():
    # "a" + "\" + Enter (continuation) + "b" + Enter (submit) → "a\nb"
    assert await _run_editor("a\\\rb\r") == "a\nb"


async def test_editor_alt_enter_inserts_newline():
    # "a" + Esc+Enter (newline) + "b" + Enter (submit) → "a\nb"
    assert await _run_editor("a\x1b\rb\r") == "a\nb"


async def test_editor_ctrl_r_reverse_search():
    """Ctrl+R searches history; Enter must accept the match, not our submit override."""
    from prompt_toolkit.history import InMemoryHistory

    hist = InMemoryHistory()
    hist.append_string("deploy the thing")
    hist.append_string("hello world")
    with create_pipe_input() as inp:
        # Ctrl+R, type 'deploy', Enter (accept search), Enter (submit the accepted line).
        inp.send_text("\x12deploy\r\r")
        session = PromptSession(
            input=inp,
            output=DummyOutput(),
            key_bindings=_build_key_bindings(_PasteRegistry()),
            multiline=True,
            history=hist,
        )
        result = await asyncio.wait_for(session.prompt_async(), timeout=10)
    assert result == "deploy the thing"


# ---------------------------------------------------------------------------
# Big-paste collapsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "collapses"),
    [("one line", False), ("a\nb", False), ("a\nb\nc", False), ("a\nb\nc\nd", True), ("\n".join("x" * 9), True)],
)
def test_should_collapse_paste(text, collapses):
    assert _should_collapse_paste(text) is collapses


def test_paste_registry_roundtrip():
    pastes = _PasteRegistry()
    blob = "\n".join(f"line {i}" for i in range(6))
    label = pastes.collapse(blob)
    assert label == "[pasted 6 lines]"
    assert pastes.expand(f"see {label} please") == f"see {blob} please"


def test_paste_registry_disambiguates_same_size():
    pastes = _PasteRegistry()
    a = "\n".join(["a"] * 6)
    b = "\n".join(["b"] * 6)
    label_a = pastes.collapse(a)
    label_b = pastes.collapse(b)
    assert label_a != label_b
    assert pastes.expand(f"{label_a}|{label_b}") == f"{a}|{b}"


async def test_paste_registry_cleared_after_turn(agent_fixture, arcana_home):
    # A collapsed paste is expanded for the message that carried it, then the
    # registry resets — placeholders must not leak into later turns.
    received: list[str] = []
    c = _make_controller(arcana_home, agent_fixture, _mock_runtime(received=received))
    blob = "\n".join(f"line {i}" for i in range(6))
    label = c.pastes.collapse(blob)
    await _feed(c, [f"here: {label}"])
    assert received == [f"here: {blob}"]  # expanded before the turn ran
    assert c.pastes._map == {}  # …and the map was cleared afterward


async def test_editor_collapses_big_paste():
    pastes = _PasteRegistry()
    blob = "\n".join(f"line {i}" for i in range(6))
    # Paste the blob (bracketed), then Enter to submit.
    raw = await _run_editor(_bracketed(blob) + "\r", pastes)
    assert raw == "[pasted 6 lines]"  # the editor shows a placeholder, not the blob
    assert pastes.expand(raw) == blob  # …which expands back to the full text


async def test_editor_small_paste_stays_inline():
    pastes = _PasteRegistry()
    raw = await _run_editor(_bracketed("a\nb") + "\r", pastes)
    assert raw == "a\nb"  # short paste is inserted as-is, no placeholder


def test_slash_completer_completes_command_names(arcana_home):
    reg = AgentRegistry(arcana_home / "agents")
    comp = _SlashCompleter(reg)
    out = [c.text for c in comp.get_completions(Document("/me"), CompleteEvent())]
    assert "/memory" in out


def test_slash_completer_completes_agent_names_after_switch(agent_fixture, arcana_home):
    reg = AgentRegistry(arcana_home / "agents")
    comp = _SlashCompleter(reg)
    out = [c.text for c in comp.get_completions(Document("/switch sc"), CompleteEvent())]
    assert "scout" in out


def test_slash_completer_ignores_plain_text(arcana_home):
    reg = AgentRegistry(arcana_home / "agents")
    comp = _SlashCompleter(reg)
    out = list(comp.get_completions(Document("hello"), CompleteEvent()))
    assert out == []


def test_editor_binds_ctrl_l_clear():
    from prompt_toolkit.keys import Keys

    kb = _build_key_bindings(_PasteRegistry())
    assert any(Keys.ControlL in binding.keys for binding in kb.bindings)


# ---------------------------------------------------------------------------
# Per-agent input history
# ---------------------------------------------------------------------------
def test_history_is_scoped_per_agent(agent_fixture, arcana_home):
    from prompt_toolkit.history import FileHistory

    history = _agent_history(agent_fixture.id)
    assert isinstance(history, FileHistory)
    expected = arcana_home / "agents" / str(agent_fixture.id) / "chat_history"
    assert history.filename == str(expected)


# ---------------------------------------------------------------------------
# Full-screen app wiring
# ---------------------------------------------------------------------------
def test_build_app_focuses_input_with_history_and_autosuggest(agent_fixture, arcana_home):
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.history import FileHistory

    from arcana_cli.commands.chat.app import _build_app

    c = _make_controller(arcana_home, agent_fixture, _mock_runtime())
    app_obj = _build_app(c)
    buf = c._input_buffer
    assert buf is not None
    assert isinstance(buf.auto_suggest, AutoSuggestFromHistory)
    assert isinstance(buf.history, FileHistory)
    # Slash-command menu pops as you type (completer only fires on a leading "/").
    assert isinstance(buf.completer, chat_editor._SlashCompleter)
    assert buf.complete_while_typing() is True
    # The input is the focus target, so typing goes to the pinned box, not the transcript.
    assert app_obj.layout.current_window is not None
    assert app_obj.full_screen is True


def test_editor_binds_tab_completion():
    from prompt_toolkit.keys import Keys

    kb = _build_key_bindings(_PasteRegistry())
    keys = {k for binding in kb.bindings for k in binding.keys}
    assert Keys.ControlI in keys or Keys.Tab in keys  # Tab triggers/cycles completion


def test_editor_binds_escape_to_close_menu():
    from prompt_toolkit.keys import Keys

    kb = _build_key_bindings(_PasteRegistry())
    # A lone-Escape binding (distinct from the Esc+Enter newline chord) closes the menu.
    assert any(tuple(b.keys) == (Keys.Escape,) for b in kb.bindings)


# ---------------------------------------------------------------------------
# Friendlier turn errors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ConnectionError("Connection refused"), "provider running"),
        (TimeoutError("request timed out"), "timed out"),
        (RuntimeError("model 'llama' not found, try pulling it"), "pulling it"),
        (ValueError("something odd"), "something odd"),
    ],
)
def test_friendly_error(exc, expected):
    from arcana_cli.commands.chat.controller import _friendly_error

    assert expected in _friendly_error(exc)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The reported case: bold vectors render as Unicode math-bold.
        (r"Key ($\mathbf{K}$) and Value ($\mathbf{V}$)", "Key (𝐊) and Value (𝐕)"),
        (r"scales $O(N^2)$ not $O(N)$", "scales O(N²) not O(N)"),
        (r"$\alpha + \beta = \gamma$", "α + β = γ"),
        (r"the reals $\mathbb{R}$", "the reals ℝ"),
        (r"$x_1$ and $x_{10}$", "x₁ and x₁₀"),
        (r"\(E = mc^2\)", "E = mc²"),
        # Prices are prose, not math — the dollars must survive.
        ("it costs $5 and $6 total", "it costs $5 and $6 total"),
        # Code spans/fences are left untouched.
        (r"use `$x^2$` inline", r"use `$x^2$` inline"),
        # Anything we can't convert cleanly stays as raw TeX.
        (r"display $$\frac{a}{b}$$", r"display $$\frac{a}{b}$$"),
    ],
)
def test_normalize_math(text, expected):
    from arcana_cli.ui.mathtext import normalize_math

    assert normalize_math(text) == expected


def test_render_reply_converts_inline_math():
    from arcana_cli.commands.chat.render import _render_reply, _Transcript

    t = _Transcript()
    t.append(_render_reply(r"The vector $\mathbf{K}$ scales as $O(N^2)$."))
    out = t.plain_text()
    assert "𝐊" in out
    assert "O(N²)" in out
    assert "mathbf" not in out  # the raw TeX macro is gone


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("just text", [("answer", "just text")]),
        ("<think>reasoning</think>the answer", [("think", "reasoning"), ("answer", "the answer")]),
        ("prefix<think>r</think>", [("answer", "prefix"), ("think", "r")]),
        ("keep<think>still thinking", [("answer", "keep"), ("think", "still thinking")]),
        ("<thinking>r</thinking>a", [("think", "r"), ("answer", "a")]),
    ],
)
def test_split_reasoning(text, expected):
    from arcana_cli.commands.chat.render import _split_reasoning

    assert _split_reasoning(text) == expected
