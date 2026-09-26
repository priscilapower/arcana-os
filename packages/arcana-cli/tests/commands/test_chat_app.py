"""Tests for the chat session app: turns on screen, the session's keys, and the exit path.

The app runs headless under Textual's Pilot with a stand-in runtime agent, so a
test types into the real chat input and watches the real transcript, live block
and status bar. :func:`run_chat` is driven end to end with the model gateway and
runtime build stubbed out.
"""

import asyncio
import io
from typing import Any

import pytest
import typer
from rich.console import Console
from rich.panel import Panel
from textual.pilot import Pilot

import arcana_cli.commands.chat.app as chat_app
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.types.card import Card
from arcana.types.session import MessageRole, SessionStatus
from arcana_cli._render import EXIT_ERROR
from arcana_cli.commands.chat.app import ChatApp, run_chat
from arcana_cli.tui.card_picker import CardPickerScreen
from arcana_cli.tui.history import AgentHistory
from arcana_cli.tui.screens import ConfirmScreen
from arcana_cli.ui.renderer.textual_renderer import TextualRenderer
from tests.support.chat import (
    FakeFederation,
    FakeGateway,
    create_agent,
    make_controller,
    mock_runtime,
    patch_build,
    use_arcana_home,
)
from tests.support.chat_app import ChatSession, chat_session
from tests.support.tui import arcana_pilot, run_inline_headless, wait_for_screen


@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    return use_arcana_home(tmp_path, monkeypatch)


@pytest.fixture()
def agent_fixture(arcana_home):
    return create_agent(arcana_home)


# ── a turn on screen ──────────────────────────────────────────────────────


async def test_a_full_turn(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime(chunks=["Hello", " world"])) as s:
        await s.send("hi")
        await s.settle()
        out = s.retained()
        assert "hi" in out
        assert "SCOUT" in out
        assert "Hello world" in out
        assert not s.app.live.display
        assert not s.app.chat_input.busy


async def test_the_session_opens_with_the_header_and_status_line(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.pilot.pause()
        assert "The Hermit" in s.h.visible_text()
        status = str(s.app.status_bar.render_line(0).text)
        assert f"#{str(s.c.session.id)[:4]}" in status
        assert "memory on" in status


async def test_streaming_shows_partial_text_before_the_turn_completes(agent_fixture, arcana_home):
    gate, started = asyncio.Event(), asyncio.Event()
    runtime = mock_runtime(chunks=["Partial **bold**", " and the rest"], gate=gate, started=started)
    async with chat_session(arcana_home, agent_fixture, runtime) as s:
        await s.send("hi")
        await s.until(started.is_set, "the stream started")
        await s.until(lambda: "Partial bold" in s.live_text(), "the partial reply rendered")
        assert s.app.live.display
        assert s.c.busy
        assert "Partial" not in s.retained()  # not in the transcript until it's done
        gate.set()
        await s.settle()
        assert "Partial bold and the rest" in s.retained()
        assert not s.app.live.display


async def test_the_live_block_shows_thinking_until_the_first_token(agent_fixture, arcana_home):
    gate = asyncio.Event()

    async def _slow(prompt: str, *, session: Any = None, context: Any = None):
        await gate.wait()
        yield "done"

    runtime = mock_runtime()
    runtime.stream = _slow
    async with chat_session(arcana_home, agent_fixture, runtime) as s:
        await s.send("hi")
        await s.until(lambda: s.app.live.display, "the live block opened")
        assert "…thinking" in s.live_text()
        gate.set()
        await s.settle()
        assert "…thinking" not in s.retained()


async def test_enter_while_busy_keeps_the_text(agent_fixture, arcana_home):
    gate, started = asyncio.Event(), asyncio.Event()
    async with chat_session(
        arcana_home, agent_fixture, mock_runtime(chunks=["a", "b"], gate=gate, started=started)
    ) as s:
        await s.send("first")
        await s.until(started.is_set, "the stream started")
        assert s.app.chat_input.busy
        await s.send("second")
        assert s.app.chat_input.text == "second"
        gate.set()
        await s.settle()
        assert "second" not in s.retained()


async def test_blank_enter_is_ignored(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        before = len(s.app.transcript.retained)
        await s.pilot.press("enter", "space", "enter")
        await s.pilot.pause()
        assert len(s.app.transcript.retained) == before
        assert not s.c.busy


# ── Ctrl+C / Ctrl+D / /exit ───────────────────────────────────────────────


async def test_ctrl_c_mid_stream_keeps_the_partial_text_and_notes_the_cancel(agent_fixture, arcana_home):
    gate, started = asyncio.Event(), asyncio.Event()
    async with chat_session(
        arcana_home, agent_fixture, mock_runtime(chunks=["Half a", " reply"], gate=gate, started=started)
    ) as s:
        await s.send("hi")
        await s.until(started.is_set, "the stream started")
        await s.pilot.press("ctrl+c")
        await s.settle()
        out = s.retained()
        assert "Half a" in out
        assert "…cancelled" in out
        assert "reply" not in out.split("Half a", 1)[1].split("cancelled", 1)[0]
        assert not s.c.exited  # a cancel is not a quit
        assert not s.app.chat_input.busy
        # The session is still usable after a cancel.
        await s.pilot.press(*"/help", "enter")
        await s.settle()
        assert "In-session commands" in s.retained()


async def test_ctrl_c_idle_quits(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.pilot.press("ctrl+c")
        await s.pilot.pause()
        assert s.c.exited


async def test_ctrl_c_is_not_copy_in_the_input(agent_fixture, arcana_home):
    # TextArea binds Ctrl+C to copy; the session's priority binding wins.
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.pilot.press(*"draft")
        await s.pilot.press("ctrl+c")
        await s.pilot.pause()
        assert s.c.exited


async def test_ctrl_d_on_an_empty_prompt_quits(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.pilot.press("ctrl+d")
        await s.pilot.pause()
        assert s.c.exited


async def test_ctrl_d_with_text_deletes_forward(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.pilot.press(*"ab", "home", "ctrl+d")
        await s.pilot.pause()
        assert not s.c.exited
        assert s.app.chat_input.text == "b"


async def test_ctrl_d_during_a_turn_does_not_quit(agent_fixture, arcana_home):
    gate, started = asyncio.Event(), asyncio.Event()
    async with chat_session(
        arcana_home, agent_fixture, mock_runtime(chunks=["a", "b"], gate=gate, started=started)
    ) as s:
        await s.send("hi")
        await s.until(started.is_set, "the stream started")
        await s.pilot.press("ctrl+d")
        await s.pilot.pause()
        assert not s.c.exited
        gate.set()
        await s.settle()


async def test_slash_exit_quits(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.send("/exit")
        await s.pilot.pause()
        assert s.c.exited


# ── dialogs inside a turn ─────────────────────────────────────────────────


async def test_a_turn_can_await_a_dialog_and_ctrl_c_takes_it_down(agent_fixture, arcana_home):
    """A turn is an app worker, so a tool confirmer can ask; Ctrl+C cancels the turn and the dialog."""
    app = ChatApp()
    renderer = TextualRenderer(app)
    answers: list[bool] = []

    async def _asking(prompt: str, *, session: Any = None, context: Any = None):
        yield "Checking. "
        answers.append(await renderer.confirm("Allow write_file?"))
        yield "never"

    runtime = mock_runtime()
    runtime.stream = _asking
    controller = make_controller(arcana_home, agent_fixture, runtime, renderer=renderer)
    app.controller = controller
    async with arcana_pilot(app=app) as h:
        await h.pilot.press(*"go", "enter")
        await wait_for_screen(h.pilot, ConfirmScreen)
        await h.pilot.press("ctrl+c")
        s = ChatSession(h, controller)
        await s.settle()
        assert not isinstance(app.screen, ConfirmScreen)
        assert answers == []
        assert "…cancelled" in s.retained()
        assert "Checking." in s.retained()


async def test_a_turn_dialog_answer_reaches_the_turn(agent_fixture, arcana_home):
    app = ChatApp()
    renderer = TextualRenderer(app)

    async def _asking(prompt: str, *, session: Any = None, context: Any = None):
        yield "allowed" if await renderer.confirm("Allow?") else "denied"

    runtime = mock_runtime()
    runtime.stream = _asking
    controller = make_controller(arcana_home, agent_fixture, runtime, renderer=renderer)
    app.controller = controller
    async with arcana_pilot(app=app) as h:
        await h.pilot.press(*"go", "enter")
        await wait_for_screen(h.pilot, ConfirmScreen)
        await h.pilot.press("y")
        s = ChatSession(h, controller)
        await s.settle()
        assert "allowed" in s.retained()


# ── slash commands that touch the app ─────────────────────────────────────


async def test_switch_rescopes_history_and_completion(agent_fixture, arcana_home, monkeypatch):
    sage = create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    patch_build(monkeypatch, mock_runtime())
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        assert s.app.chat_input.recall.path == AgentHistory.for_agent(agent_fixture.id).path
        assert sorted(s.app.chat_input.agent_names()) == ["sage", "scout"]
        await s.send("/switch sage")
        await s.settle()
        assert s.app.chat_input.recall.path == AgentHistory.for_agent(sage.id).path
        assert "switched to sage" in s.retained()
        assert f"#{str(s.c.session.id)[:4]}" in str(s.app.status_bar.render_line(0).text)
    # The /switch line itself belongs to the agent it was typed to.
    assert AgentHistory.for_agent(agent_fixture.id).entries == ("/switch sage",)


async def test_bare_switch_opens_the_agent_picker_and_switches(agent_fixture, arcana_home, monkeypatch):
    sage = create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    patch_build(monkeypatch, mock_runtime())
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.send("/switch")
        screen = await wait_for_screen(s.pilot, CardPickerScreen)
        assert isinstance(screen, CardPickerScreen)
        await s.pilot.press(*"sage")
        await s.pilot.pause()
        preview = screen.preview.content
        assert isinstance(preview, Panel)
        assert "The High Priestess" in str(preview.renderable)  # the agent's primary card
        await s.pilot.press("enter")
        await s.settle()
        assert s.c.record.id == sage.id
        assert s.app.chat_input.recall.path == AgentHistory.for_agent(sage.id).path
        assert f"#{str(s.c.session.id)[:4]}" in str(s.app.status_bar.render_line(0).text)
        assert "switched to sage" in s.retained()
        assert s.app.focused is s.app.chat_input


async def test_bare_switch_escape_leaves_the_session_unchanged(agent_fixture, arcana_home, monkeypatch):
    create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    patch_build(monkeypatch, mock_runtime())
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        session = s.c.session
        await s.send("/switch")
        await wait_for_screen(s.pilot, CardPickerScreen)
        await s.pilot.press("escape")
        await s.settle()
        assert s.c.record.id == agent_fixture.id
        assert s.c.session is session
        assert s.app.chat_input.recall.path == AgentHistory.for_agent(agent_fixture.id).path
        assert s.app.focused is s.app.chat_input


async def test_ctrl_c_in_the_agent_picker_cancels_the_switch(agent_fixture, arcana_home, monkeypatch):
    create_agent(arcana_home, name="sage", card=Card.HIGH_PRIESTESS)
    patch_build(monkeypatch, mock_runtime())
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.send("/switch")
        await wait_for_screen(s.pilot, CardPickerScreen)
        await s.pilot.press("ctrl+c")
        await s.settle()
        assert not isinstance(s.app.screen, CardPickerScreen)
        assert s.c.record.id == agent_fixture.id
        assert s.app.is_running  # cancelled the /switch, didn't quit the session


async def test_no_memory_updates_the_status_line(agent_fixture, arcana_home, monkeypatch):
    patch_build(monkeypatch, mock_runtime())
    async with chat_session(arcana_home, agent_fixture, mock_runtime()) as s:
        await s.send("/no-memory")
        await s.settle()
        assert "memory off" in str(s.app.status_bar.render_line(0).text)


async def test_clear_empties_the_screen_but_the_exit_replay_keeps_the_session(agent_fixture, arcana_home):
    async with chat_session(arcana_home, agent_fixture, mock_runtime(chunks=["reply text"])) as s:
        await s.send("hi")
        await s.settle()
        await s.send("/clear")
        await s.settle()
        await s.pilot.pause()
        visible = s.h.visible_text()
        assert "reply text" not in visible
        assert "The Hermit" in visible  # header redrawn
        assert "reply text" in s.retained()


# ── run_chat: open, exit, fail-safe ───────────────────────────────────────


@pytest.fixture()
def stub_runtime(monkeypatch) -> FakeFederation:
    """No model stack: a fake gateway, and a runtime whose federation records its close."""
    federation = FakeFederation()
    monkeypatch.setattr(chat_app, "ModelGateway", FakeGateway)
    patch_build(monkeypatch, mock_runtime(chunks=["pong"]), federation=federation)
    return federation


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=100, color_system=None), out


async def _run(arcana_home, record, **kwargs: Any) -> None:
    await run_chat(
        reg=AgentRegistry(arcana_home / "agents"),
        sm=SessionManager(arcana_home / "agents"),
        record=record,
        session=kwargs.pop("session", None),
        memory_off=False,
        mouse=None,
        **kwargs,
    )


async def test_run_chat_closes_the_session_and_federation_then_prints_the_resume_hint(
    agent_fixture, arcana_home, monkeypatch, stub_runtime
):
    async def session(pilot: Pilot[Any]) -> None:
        app = pilot.app
        assert isinstance(app, ChatApp) and app.controller is not None
        await pilot.press(*"ping", "enter")
        for _ in range(100):
            await pilot.pause()
            if len(app.controller.session.messages) == 2 and not app.controller.busy:
                break
        await pilot.press("ctrl+c")

    run_inline_headless(monkeypatch, ChatApp, session)
    console, out = _console()
    await _run(arcana_home, agent_fixture, console=console, notes=("[dim]The World opened this chat with scout.[/]",))

    [saved] = SessionManager(arcana_home / "agents").list_sessions(agent_fixture.id)
    assert saved.status == SessionStatus.COMPLETED
    assert [m.role for m in saved.messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert stub_runtime.closed
    printed = out.getvalue()
    assert "The World opened this chat with scout" in printed
    assert printed.index("pong") < printed.index(f"--session {saved.id}")  # the hint follows the replay


async def test_run_chat_resumes_the_given_session(agent_fixture, arcana_home, monkeypatch, stub_runtime):
    sm = SessionManager(arcana_home / "agents")
    prior = sm.start(agent_fixture.id)
    prior.add_message(MessageRole.USER, "earlier question")
    prior.add_message(MessageRole.ASSISTANT, "earlier answer")
    sm.close(prior)

    async def session(pilot: Pilot[Any]) -> None:
        await pilot.pause()
        await pilot.press("ctrl+d")

    run_inline_headless(monkeypatch, ChatApp, session)
    console, out = _console()
    await _run(arcana_home, agent_fixture, console=console, session=sm.load(agent_fixture.id, prior.id))
    printed = out.getvalue()
    assert "earlier question" in printed
    assert f"--session {prior.id}" in printed


async def test_a_crash_inside_the_app_still_closes_the_session_and_federation(
    agent_fixture, arcana_home, monkeypatch, stub_runtime
):
    def _boom(self: ChatApp, event: Any) -> None:
        raise RuntimeError("handler blew up")

    monkeypatch.setattr(ChatApp, "on_chat_input_submitted", _boom)

    async def session(pilot: Pilot[Any]) -> None:
        await pilot.press(*"hi", "enter")
        await pilot.pause()

    run_inline_headless(monkeypatch, ChatApp, session)
    console, out = _console()
    with pytest.raises(typer.Exit) as exc_info:
        await _run(arcana_home, agent_fixture, console=console)
    assert exc_info.value.exit_code == EXIT_ERROR
    [saved] = SessionManager(arcana_home / "agents").list_sessions(agent_fixture.id)
    assert saved.status == SessionStatus.COMPLETED
    assert stub_runtime.closed
    assert "--session" in out.getvalue()


async def test_a_failure_before_the_app_starts_still_closes_the_session(agent_fixture, arcana_home, monkeypatch):
    monkeypatch.setattr(chat_app, "ModelGateway", FakeGateway)

    async def _fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("memory store unavailable")

    monkeypatch.setattr(chat_app, "build_session_runtime", _fail)
    with pytest.raises(RuntimeError, match="memory store unavailable"):
        await _run(arcana_home, agent_fixture, console=_console()[0])
    [saved] = SessionManager(arcana_home / "agents").list_sessions(agent_fixture.id)
    assert saved.status == SessionStatus.COMPLETED
