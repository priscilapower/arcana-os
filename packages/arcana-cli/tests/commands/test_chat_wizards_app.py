"""The setup wizards on screen: their dialogs inside the running session, cancelling them, and OAuth sign-in.

The real chat app runs headless under Pilot; a wizard's questions are dialogs
over the session, answered with key presses. These tests pin what the user
sees and what is left on disk: Esc at any step writes nothing, Ctrl+C cancels
the wizard (and closes an OAuth loopback listener), a failure is a note and the
input box has the focus again, and no secret or token reaches the transcript,
the exit replay, the input history or a log.
"""

import io
import logging
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from textual.pilot import Pilot

import arcana_cli.commands.agent as agent_mod
import arcana_cli.commands.chat.app as chat_app
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.models import ConnectionStore
from arcana.tools.adapters.mcp import stdio_errlog
from arcana_cli.commands.chat.app import STDIO_ERRLOG, ChatApp, run_chat
from arcana_cli.tui.history import agent_history_path
from arcana_cli.tui.screens import PromptScreen, WaitScreen
from arcana_cli.ui.renderer import CANCELLED
from tests.support.chat import FakeGateway, create_agent, mock_runtime, patch_build, use_arcana_home
from tests.support.chat_app import ChatSession, chat_session
from tests.support.oauth import ACCESS_TOKEN, ISSUER, REFRESH_TOKEN, USER_CODE, install_fake_as, listener_is_closed
from tests.support.tui import run_inline_headless, wait_for_screen
from tests.support.world import World, install_world

API_KEY = "sk-typed-in-a-dialog"

#: ``/providers add`` with every answer left to the dialogs: provider, model id, name, API key.
ADD_ANTHROPIC_ANSWERS = ["anthropic", "claude-x", "work", API_KEY]

OAUTH_ADD = f"/providers add --provider anthropic --model-id claude-x --name work --oauth --issuer {ISSUER}"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    use_arcana_home(tmp_path, monkeypatch)
    return install_world(tmp_path, monkeypatch)


def _session(home: World, runtime: Any = None) -> Any:
    return chat_session(home.root, create_agent(home.root), runtime if runtime is not None else mock_runtime())


def _nothing_saved(home: World) -> bool:
    return ConnectionStore(home.models).get_by_name("work") is None and home.keyring == {}


async def _idle_with_focus(s: ChatSession) -> None:
    await s.settle()
    await s.until(lambda: s.input_focused, "the input box got the focus back")
    assert not s.app.chat_input.busy


def _shown(screen: WaitScreen) -> str:
    """What the wait dialog shows, as plain text."""
    console = Console(width=100, record=True, file=io.StringIO(), color_system=None)
    if screen.shown is not None:
        console.print(screen.shown)
    return console.export_text()


def _history(s: ChatSession) -> str:
    return "\n".join(s.app.chat_input.recall.entries)


# ── a wizard's dialogs ────────────────────────────────────────────────────


async def test_providers_add_runs_through_its_dialogs_and_keeps_the_key_out_of_sight(home, caplog):
    caplog.set_level(logging.DEBUG)
    async with _session(home) as s:
        await s.enter("/providers add")
        for answer in ADD_ANTHROPIC_ANSWERS:
            await wait_for_screen(s.pilot, PromptScreen)
            assert s.app.chat_input.busy  # Enter in the box can't start a turn meanwhile
            await s.answer(answer)
        await _idle_with_focus(s)

        conn = ConnectionStore(home.models).get_by_name("work")
        assert conn is not None and str(conn.provider) == "anthropic"
        assert list(home.keyring.values()) == [API_KEY]
        assert "Added connection 'work'" in s.retained()
        assert "(hidden)" in s.retained()
        assert API_KEY not in s.retained()
        assert API_KEY not in s.h.visible_text()
        assert API_KEY not in _history(s)
    assert API_KEY not in caplog.text


@pytest.mark.parametrize("step", range(len(ADD_ANTHROPIC_ANSWERS)))
async def test_escape_at_any_step_saves_nothing(home, step):
    async with _session(home) as s:
        await s.enter("/providers add")
        for answer in ADD_ANTHROPIC_ANSWERS[:step]:
            await wait_for_screen(s.pilot, PromptScreen)
            await s.answer(answer)
        await wait_for_screen(s.pilot, PromptScreen)
        await s.pilot.press("escape")
        await _idle_with_focus(s)
        assert CANCELLED in s.retained()
        assert _nothing_saved(home)
        assert not isinstance(s.app.screen, PromptScreen)


async def test_ctrl_c_cancels_a_wizard_mid_dialog(home):
    async with _session(home) as s:
        await s.enter("/providers add --provider anthropic")
        await wait_for_screen(s.pilot, PromptScreen)
        await s.pilot.press("ctrl+c")
        await _idle_with_focus(s)
        assert "…cancelled" in s.retained()
        assert not isinstance(s.app.screen, PromptScreen)
        assert _nothing_saved(home)
        assert not s.c.exited


async def test_a_wizard_that_raises_is_a_note_and_the_input_has_the_focus(home, monkeypatch):
    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(agent_mod, "create_agent", boom)
    async with _session(home) as s:
        await s.enter("/agent create")
        await _idle_with_focus(s)
        assert "/agent create failed: keychain locked" in s.retained()
        assert not s.c.exited


async def test_agent_create_picks_its_card_in_the_session(home, monkeypatch):
    patch_build(monkeypatch, mock_runtime())
    async with _session(home) as s:
        await s.enter("/agent create --name oracle --model ollama/hermes-3")
        await s.until(lambda: type(s.app.screen).__name__ == "CardPickerScreen", "the card picker opened")
        await s.pilot.press("enter")  # the first card
        await s.until(lambda: type(s.app.screen).__name__ == "ConfirmScreen", "the modifier question")
        await s.pilot.press("n")
        await _idle_with_focus(s)
        assert "Agent 'oracle' created." in s.retained()
        assert "oracle" in [r.name for r in AgentRegistry(home.agents).list()]


async def test_a_secret_on_the_command_line_never_reaches_the_transcript_or_history(home):
    async with _session(home) as s:
        await s.enter(f"/providers add --provider anthropic --model-id m --name work --api-key {API_KEY}")
        await _idle_with_focus(s)
        assert API_KEY not in s.retained()
        assert "--api-key (hidden)" in s.retained()
        assert API_KEY not in _history(s)
        assert _nothing_saved(home)


# ── OAuth sign-in in the session ─────────────────────────────────────────


async def test_device_sign_in_shows_the_code_in_a_dialog_then_stores_the_token(home, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    server, _ = install_fake_as(monkeypatch)
    async with _session(home) as s:
        await s.enter(f"{OAUTH_ADD} --device")
        screen = await wait_for_screen(s.pilot, WaitScreen)
        assert isinstance(screen, WaitScreen)
        await s.until(lambda: USER_CODE in _shown(screen), "the code is shown")
        assert s.c.busy
        server.approved = True
        await _idle_with_focus(s)

        conn = ConnectionStore(home.models).get_by_name("work")
        assert conn is not None and conn.credential_ref is not None
        assert ACCESS_TOKEN in home.keyring[conn.credential_ref]
        assert "Signed in" in s.retained()
        for text in (s.retained(), _history(s), caplog.text):
            assert ACCESS_TOKEN not in text and REFRESH_TOKEN not in text
        assert USER_CODE not in s.retained()  # the dialog showed it; the transcript keeps nothing of it


async def test_escape_on_the_device_code_dialog_cancels_the_sign_in(home, monkeypatch):
    install_fake_as(monkeypatch)
    async with _session(home) as s:
        await s.enter(f"{OAUTH_ADD} --device")
        await wait_for_screen(s.pilot, WaitScreen)
        await s.pilot.press("escape")
        await _idle_with_focus(s)
        assert CANCELLED in s.retained()
        assert not isinstance(s.app.screen, WaitScreen)
        assert _nothing_saved(home)


async def test_browser_sign_in_completes_over_a_loopback_listener_that_is_then_closed(home, monkeypatch):
    _, browser = install_fake_as(monkeypatch, complete_browser=True)
    async with _session(home) as s:
        await s.enter(OAUTH_ADD)
        await _idle_with_focus(s)
        assert "Added connection 'work'" in s.retained()
        assert browser.redirect_uri.startswith("http://127.0.0.1:")
        assert await listener_is_closed(browser.redirect_uri)
        assert ACCESS_TOKEN not in s.retained()


@pytest.mark.parametrize(("key", "note"), [("escape", CANCELLED), ("ctrl+c", "…cancelled")])
async def test_calling_off_a_browser_sign_in_closes_the_listener_and_saves_nothing(home, monkeypatch, key, note):
    _, browser = install_fake_as(monkeypatch, complete_browser=False)
    async with _session(home) as s:
        await s.enter(OAUTH_ADD)
        screen = await wait_for_screen(s.pilot, WaitScreen)
        await s.until(lambda: browser.opened, "the browser was sent to sign in")
        assert "127.0.0.1" in _shown(screen)  # the URL, to open by hand
        # (Not probed while open: the one-shot listener would take the probe as the callback.)
        await s.pilot.press(key)
        await _idle_with_focus(s)
        assert note in s.retained()
        assert await listener_is_closed(browser.redirect_uri)
        assert _nothing_saved(home)


# ── the whole session: exit replay, history file, logs ───────────────────


async def test_no_secret_reaches_the_exit_replay_history_file_or_logs(home, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(chat_app, "ModelGateway", FakeGateway)
    patch_build(monkeypatch, mock_runtime())
    record = create_agent(home.root)
    seen_errlog: list[object] = []

    async def session(pilot: Pilot[Any]) -> None:
        app = pilot.app
        assert isinstance(app, ChatApp) and app.controller is not None
        seen_errlog.append(stdio_errlog.get())
        app.chat_input.load_text("/providers add")
        await pilot.pause()
        await pilot.press("enter")
        for answer in ADD_ANTHROPIC_ANSWERS:
            await wait_for_screen(pilot, PromptScreen)
            await pilot.press(*answer, "enter")
        for _ in range(100):
            await pilot.pause()
            if not app.controller.busy:
                break
        await pilot.press("ctrl+c")

    run_inline_headless(monkeypatch, ChatApp, session)
    out = io.StringIO()
    await run_chat(
        reg=AgentRegistry(home.agents),
        sm=SessionManager(home.agents),
        record=record,
        session=None,
        memory_off=False,
        mouse=None,
        console=Console(file=out, width=100, color_system=None),
    )

    assert list(home.keyring.values()) == [API_KEY]
    assert "Added connection 'work'" in out.getvalue()
    assert API_KEY not in out.getvalue()
    history = agent_history_path(record.id, home.root)
    assert not history.exists() or API_KEY not in history.read_text()
    for log in home.root.rglob("*.log*"):
        assert API_KEY not in log.read_text(errors="ignore")
    assert API_KEY not in caplog.text
    # While the app ran, a stdio MCP server's stderr went to the session's log file.
    [sink] = seen_errlog
    assert getattr(sink, "name", None) == str(home.root / STDIO_ERRLOG)
    assert stdio_errlog.get() is None
