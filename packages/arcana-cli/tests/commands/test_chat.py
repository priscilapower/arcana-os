"""Tests for ``arcana chat``: the command's start-up checks and the session logic.

All of the session's behaviour lives in :class:`_ChatController`, which writes
through the renderer port. These tests hand it a
:class:`~tests.support.renderer.RecordingRenderer`, ``await
controller.submit(line)`` and assert on what was rendered, so no terminal is
needed. The app itself (keys, streaming on screen, the exit path) is covered by
``test_chat_app.py``.
"""

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from rich.text import Text
from typer.testing import CliRunner

import arcana_cli.commands.chat.app as chat_app
from arcana.agents.session_manager import SessionManager
from arcana.types.card import Card
from arcana.types.session import Message, MessageRole
from arcana_cli._render import EXIT_ERROR
from arcana_cli.commands.chat.controller import _ChatController, _friendly_error
from arcana_cli.commands.chat.render import (
    _footer_line,
    _live_reply,
    _render_reply,
    _replay_blocks,
    _split_reasoning,
)
from arcana_cli.main import app
from arcana_cli.ui.mathtext import normalize_math
from tests.support.chat import create_agent, make_controller, mock_runtime, patch_build, use_arcana_home
from tests.support.renderer import RecordingRenderer

runner = CliRunner()


@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    return use_arcana_home(tmp_path, monkeypatch)


@pytest.fixture()
def agent_fixture(arcana_home):
    return create_agent(arcana_home)


@pytest.fixture()
def run_chat_calls(monkeypatch) -> list[dict[str, Any]]:
    """Stands in for the session app: records each ``run_chat`` call instead of starting Textual."""
    calls: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(chat_app, "run_chat", _fake)
    return calls


class _AllowAll:
    async def confirm(self, tool_name: str, args: dict[str, Any]) -> bool:
        return True


def _out(c: _ChatController) -> str:
    renderer = c.renderer
    assert isinstance(renderer, RecordingRenderer)
    return renderer.text()


async def _feed(controller, lines):
    """Submit a scripted sequence of lines, stopping if one exits the session."""
    for line in lines:
        await controller.submit(line)
        if controller.exited:
            break


def _plain(*blocks) -> str:
    r = RecordingRenderer()
    for b in blocks:
        r.emit(b)
    return r.text()


# ---------------------------------------------------------------------------
# Start-up checks (before the session app starts)
# ---------------------------------------------------------------------------
def test_chat_without_agent_and_no_agents_asks_for_agent(arcana_home, run_chat_calls):
    # No agents configured → the World can't open the chat → asks for --agent.
    result = runner.invoke(app, ["chat"])
    assert result.exit_code == EXIT_ERROR
    assert "--agent" in result.output
    assert run_chat_calls == []


def test_chat_agent_not_found(arcana_home, run_chat_calls):
    result = runner.invoke(app, ["chat", "--agent", "ghost"])
    assert result.exit_code == EXIT_ERROR
    assert "No agent" in result.output


def test_chat_agent_no_model(arcana_home, run_chat_calls):
    create_agent(arcana_home, name="orphan", model="")
    result = runner.invoke(app, ["chat", "--agent", "orphan"])
    assert result.exit_code == EXIT_ERROR
    assert "model" in result.output.lower()


def test_chat_session_invalid_uuid(agent_fixture, run_chat_calls):
    result = runner.invoke(app, ["chat", "--agent", "scout", "--session", "not-a-uuid"])
    assert result.exit_code == EXIT_ERROR
    assert "Invalid session id" in result.output


def test_chat_session_unknown_id(agent_fixture, run_chat_calls):
    result = runner.invoke(app, ["chat", "--agent", "scout", "--session", str(uuid4())])
    assert result.exit_code == EXIT_ERROR
    assert "not found" in result.output


def test_chat_opens_the_named_agent(agent_fixture, run_chat_calls):
    result = runner.invoke(app, ["chat", "--agent", "scout", "--no-memory"])
    assert result.exit_code == 0, result.output
    [call] = run_chat_calls
    assert call["record"].id == agent_fixture.id
    assert call["session"] is None  # a new session starts inside the app run
    assert call["memory_off"] is True
    assert call["mouse"] is None  # ui.mouse from config.json decides
    assert call["notes"] == ()


def test_chat_resumes_a_session(agent_fixture, arcana_home, run_chat_calls):
    sm = SessionManager(arcana_home / "agents")
    session = sm.start(agent_fixture.id)
    sm.close(session)
    result = runner.invoke(app, ["chat", "--agent", "scout", "--session", str(session.id)])
    assert result.exit_code == 0, result.output
    assert run_chat_calls[0]["session"].id == session.id


def test_chat_no_mouse_turns_mouse_capture_off(agent_fixture, run_chat_calls):
    runner.invoke(app, ["chat", "--agent", "scout", "--no-mouse"])
    assert run_chat_calls[0]["mouse"] is False


def test_chat_without_agent_lets_the_world_pick(agent_fixture, arcana_home, run_chat_calls):
    (arcana_home / "world.json").write_text(f'{{"default_agent_id": "{agent_fixture.id}"}}')
    result = runner.invoke(app, ["chat"])
    assert result.exit_code == 0, result.output
    [call] = run_chat_calls
    assert call["record"].id == agent_fixture.id
    assert "The World opened this chat with scout" in _plain(*(Text.from_markup(n) for n in call["notes"]))


# ---------------------------------------------------------------------------
# Bare `arcana` opens the session
# ---------------------------------------------------------------------------
def test_bare_arcana_opens_the_chat_session(agent_fixture, arcana_home, run_chat_calls):
    (arcana_home / "world.json").write_text(f'{{"default_agent_id": "{agent_fixture.id}"}}')
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    [call] = run_chat_calls
    assert call["record"].id == agent_fixture.id  # The World routed the opening agent
    assert call["mouse"] is None


def test_bare_arcana_no_mouse(agent_fixture, arcana_home, run_chat_calls):
    (arcana_home / "world.json").write_text(f'{{"default_agent_id": "{agent_fixture.id}"}}')
    runner.invoke(app, ["--no-mouse"])
    assert run_chat_calls[0]["mouse"] is False


def test_bare_arcana_without_agents_prints_guidance(arcana_home, run_chat_calls):
    result = runner.invoke(app, [])
    assert result.exit_code == EXIT_ERROR
    assert "--agent" in result.output
    assert run_chat_calls == []


def test_arcana_help_still_prints_help(arcana_home, run_chat_calls):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "chat" in result.output
    assert run_chat_calls == []


def test_a_subcommand_does_not_open_the_session(arcana_home, run_chat_calls):
    result = runner.invoke(app, ["cards", "show", "fool"])
    assert result.exit_code == 0
    assert run_chat_calls == []


# ---------------------------------------------------------------------------
# The turn loop
# ---------------------------------------------------------------------------
async def test_streams_a_turn(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(chunks=["Hello", " world"]))
    await _feed(c, ["hi", "/exit"])
    out = _out(c)
    assert "Hello world" in out  # streamed reply, rendered as markdown
    assert "scout" in out  # header shows the agent
    assert c.exited


async def test_header_shows_agent_card_and_memory(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    out = _out(c)
    assert "scout" in out
    assert "The Hermit" in out  # card slug title-cased
    assert "Memory" in out
    assert "Ctrl+J" in out  # the portable newline key


async def test_open_shows_notes_and_replays_a_resumed_session(agent_fixture, arcana_home):
    session = SessionManager(arcana_home / "agents").start(agent_fixture.id)
    session.add_message(MessageRole.USER, "what is a monad")
    session.add_message(MessageRole.ASSISTANT, "A design pattern.")
    r = RecordingRenderer()
    c = make_controller(arcana_home, agent_fixture, mock_runtime(), renderer=r, session=session)
    r.emitted.clear()
    c.open(notes=("[dim]The World opened this chat with scout.[/]",))
    out = r.text()
    assert out.index("scout") < out.index("The World opened")
    assert "Resuming" in out
    assert "what is a monad" in out


async def test_labels_user_and_agent_turns(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(chunks=["hi back"]))
    await _feed(c, ["hello there"])
    out = _out(c)
    assert "YOU" in out  # ✦ YOU block for the sent message
    assert "SCOUT" in out  # ✦ SCOUT eyebrow above the reply
    assert "hello there" in out


async def test_turn_records_messages_on_session(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(chunks=["ok"]))
    await _feed(c, ["hi"])
    roles = [m.role for m in c.session.messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


async def test_exit_sets_flag(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/exit"])
    assert c.exited


async def test_multiline_message_is_passed_through(agent_fixture, arcana_home):
    received: list[str] = []
    c = make_controller(arcana_home, agent_fixture, mock_runtime(received=received))
    await _feed(c, ["line one\nline two"])
    assert received == ["line one\nline two"]


async def test_blank_line_is_ignored(agent_fixture, arcana_home):
    received: list[str] = []
    c = make_controller(arcana_home, agent_fixture, mock_runtime(received=received))
    await _feed(c, ["   ", "hi"])
    assert received == ["hi"]  # the whitespace-only line never reached the model


async def test_the_model_gets_the_expanded_text_and_the_bubble_shows_the_placeholder(agent_fixture, arcana_home):
    received: list[str] = []
    c = make_controller(arcana_home, agent_fixture, mock_runtime(received=received))
    blob = "\n".join(f"line {i}" for i in range(6))
    await c.submit("see [pasted 6 lines]", f"see {blob}")
    assert received == [f"see {blob}"]
    out = _out(c)
    assert "[pasted 6 lines]" in out
    assert "line 5" not in out


async def test_a_pasted_slash_is_not_a_command(agent_fixture, arcana_home):
    # Slash detection runs on the input as shown, so an expanded paste that
    # starts with "/" still goes to the model.
    received: list[str] = []
    c = make_controller(arcana_home, agent_fixture, mock_runtime(received=received))
    await c.submit("[pasted 4 lines]", "/exit\na\nb\nc")
    assert received == ["/exit\na\nb\nc"]
    assert not c.exited


async def test_dims_think_block(agent_fixture, arcana_home):
    c = make_controller(
        arcana_home,
        agent_fixture,
        mock_runtime(chunks=["<think>planning the reply</think>", "The answer is 42."]),
    )
    await _feed(c, ["hi"])
    out = _out(c)
    assert "think>" not in out  # neither <think> nor </think> leak through
    assert "planning the reply" in out  # reasoning shown (dimmed)
    assert "The answer is 42." in out  # answer rendered as markdown


async def test_turn_error_does_not_crash_repl(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(error=RuntimeError("model down")))
    await _feed(c, ["hi"])
    assert "model down" in _out(c)


async def test_turn_error_text_is_not_markup(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(error=RuntimeError("bad [bold]thing[/bold]")))
    await _feed(c, ["hi"])
    assert "bad [bold]thing[/bold]" in _out(c)


async def test_connection_error_shows_hint(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(error=ConnectionError("Connection refused")))
    await _feed(c, ["hi"])
    assert "provider running" in _out(c)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
async def test_help_lists_commands(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/help"])
    out = _out(c)
    for token in ("/memory", "/card", "/switch", "/fresh", "/no-memory"):
        assert token in out


async def test_unknown_command_hints_help(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/bogus"])
    assert "Unknown command" in _out(c)


async def test_card_shows_config(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/card"])
    assert "Temperature" in _out(c)


async def test_no_memory_reports_off(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime(), federation=None, memory_off=True)
    await _feed(c, ["/memory"])
    assert "off" in _out(c).lower()


async def test_fresh_starts_new_session_with_memory(agent_fixture, arcana_home, monkeypatch):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    patch_build(monkeypatch, mock_runtime(), federation=object())
    prior = c.session.id
    await _feed(c, ["/fresh"])
    out = _out(c)
    assert "new session" in out
    assert "memory on" in out
    assert c.session.id != prior  # a genuinely new session


async def test_no_memory_slash_starts_stateless_session(agent_fixture, arcana_home, monkeypatch):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    patch_build(monkeypatch, mock_runtime(), federation=None)
    await _feed(c, ["/no-memory"])
    out = _out(c)
    assert "new session" in out
    assert "memory off" in out
    assert c.memory_off is True


async def test_rebuilt_runtimes_keep_the_confirmer(agent_fixture, arcana_home, monkeypatch):
    create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    confirmer = _AllowAll()
    c.confirmer = confirmer
    calls = patch_build(monkeypatch, mock_runtime())
    await _feed(c, ["/fresh", "/no-memory", "/switch sage"])
    assert [call["confirmer"] for call in calls] == [confirmer] * 3


async def test_switch_unknown_agent(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/switch ghost"])
    assert "No agent 'ghost'" in _out(c)


async def test_switch_requires_name(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/switch"])
    assert "Usage: /switch" in _out(c)


async def test_switch_loads_named_agent(agent_fixture, arcana_home, monkeypatch):
    create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    patch_build(monkeypatch, mock_runtime())
    await _feed(c, ["/switch sage"])
    out = _out(c)
    assert "sage" in out
    assert "explicit route" in out.lower()
    assert c.record.name == "sage"


async def test_switch_records_explicit_routing_audit(agent_fixture, arcana_home, monkeypatch):
    create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    patch_build(monkeypatch, mock_runtime())
    await _feed(c, ["/switch sage"])
    audit = arcana_home / "world" / "routing_audit.jsonl"
    assert audit.exists()
    assert '"layer":"explicit"' in audit.read_text()


async def test_save_reports_snapshot(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/save"])
    assert "session saved" in _out(c)
    sessions_dir = arcana_home / "agents" / str(agent_fixture.id) / "sessions"
    assert list(sessions_dir.glob("*.json"))  # /save flushed a snapshot to disk


async def test_clear_redraws_the_header(agent_fixture, arcana_home):
    r = RecordingRenderer()
    c = make_controller(arcana_home, agent_fixture, mock_runtime(), renderer=r)
    r.emitted.clear()
    await _feed(c, ["/clear"])
    assert "The Hermit" in r.text()  # the header, drawn again (the app clears the screen first)


# ---------------------------------------------------------------------------
# /retry
# ---------------------------------------------------------------------------
async def test_retry_reruns_last_message(agent_fixture, arcana_home):
    received: list[str] = []
    c = make_controller(arcana_home, agent_fixture, mock_runtime(chunks=["ok"], received=received))
    await _feed(c, ["hello", "/retry"])
    assert received == ["hello", "hello"]  # sent once, then re-sent by /retry
    assert "retrying" in _out(c)


async def test_retry_with_no_history(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    await _feed(c, ["/retry"])
    assert "Nothing to retry" in _out(c)


def test_start_turn_needs_an_app(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    with pytest.raises(RuntimeError, match="bind_app"):
        c.start_turn("hi")


def test_blank_start_turn_is_noop_even_without_an_app(agent_fixture, arcana_home):
    c = make_controller(arcana_home, agent_fixture, mock_runtime())
    c.start_turn("   ")  # blank input never needs the app
    assert not c.busy


# ---------------------------------------------------------------------------
# Resume replay
# ---------------------------------------------------------------------------
def test_replay_renders_prior_messages():
    session = MagicMock()
    session.messages = [
        Message(role=MessageRole.USER, content="what is a monad"),
        Message(role=MessageRole.ASSISTANT, content="A monad is a design pattern."),
    ]
    out = _plain(*_replay_blocks(session, "scout", "#888888"))
    assert "Resuming" in out
    assert "what is a monad" in out
    assert "monad is a design pattern" in out


def test_replay_caps_and_notes_hidden():
    session = MagicMock()
    session.messages = [
        Message(role=MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT, content=f"msg {i}") for i in range(12)
    ]
    out = _plain(*_replay_blocks(session, "scout", "#888888"))
    assert "showing the last 8" in out  # 12 messages → last 8 shown
    assert "msg 11" in out  # newest is present
    assert "msg 0" not in out  # oldest is trimmed


def test_replay_empty_session_renders_nothing():
    session = MagicMock()
    session.messages = []
    assert _replay_blocks(session, "scout", "#888888") == []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_footer_shows_session_and_hints():
    sid = uuid4()
    text = _footer_line(sid, memory_off=False).plain
    assert f"#{str(sid)[:4]}" in text
    for hint in ("/help", "/exit", "Ctrl+C", "memory on"):
        assert hint in text


def test_footer_reports_memory_mode():
    assert "memory off" in _footer_line(uuid4(), memory_off=True).plain


def test_live_reply_shows_thinking_until_text_arrives():
    assert "…thinking" in _plain(_live_reply(""))
    assert "…thinking" not in _plain(_live_reply("Hi"))
    assert "Hi" in _plain(_live_reply("Hi"))


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
    assert normalize_math(text) == expected


def test_render_reply_converts_inline_math():
    out = _plain(_render_reply(r"The vector $\mathbf{K}$ scales as $O(N^2)$."))
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
    assert _split_reasoning(text) == expected
