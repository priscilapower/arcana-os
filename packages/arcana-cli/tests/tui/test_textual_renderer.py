"""Tests for TextualRenderer — the renderer port over the running app, driven through Pilot."""

import io
import logging
import subprocess
import sys
from typing import Any

import pytest
import typer
from pydantic import BaseModel
from rich.console import Console
from rich.text import Text
from textual.widgets import Input
from textual.worker import WorkerFailed

from arcana_cli.tui.app import ArcanaApp
from arcana_cli.tui.screens import ConfirmScreen, MultiSelectScreen, PromptScreen, SelectScreen
from arcana_cli.ui.renderer import JsonRenderer, Question, Renderer, TtyRenderer, renderer_for
from arcana_cli.ui.renderer.port import Choice
from arcana_cli.ui.renderer.textual_renderer import HIDDEN_ANSWER, TextualRenderer
from arcana_cli.ui.theme import ok
from tests.support.tui import run_inline_headless, wait_for_screen

SECRET = "sk-live-TOPSECRET-42"


def _choices() -> list[Choice[str]]:
    return [Choice("a", "Alpha"), Choice("b", "Beta"), Choice("c", "Gamma", disabled=True), Choice("d", "Delta")]


def test_adapter_satisfies_the_protocol():
    adapters: list[Renderer] = [TextualRenderer(ArcanaApp())]
    assert len(adapters) == 1


# ── emit ──────────────────────────────────────────────────────────────────


async def test_emit_appends_to_the_transcript(tui):
    async with tui() as h:
        h.renderer.emit(Text("hello"))
        await h.pilot.pause()
        assert "hello" in h.visible_text()


async def test_emit_renders_a_string_as_markup(tui):
    async with tui() as h:
        h.renderer.emit(ok("Saved"))
        await h.pilot.pause()
        assert "✓ Saved" in h.visible_text()
        assert "[bold" not in h.visible_text()
        assert h.retained_text().strip() == "✓ Saved"


async def test_emit_pretty_prints_json_data(tui):
    class Payload(BaseModel):
        name: str

    async with tui() as h:
        h.renderer.emit({"k": 1})
        h.renderer.emit(Payload(name="hermit"))
        await h.pilot.pause()
        text = h.retained_text()
        assert "'k': 1" in text
        assert "'name': 'hermit'" in text


# ── confirm ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("key", "default", "expected"), [("y", False, True), ("n", True, False)])
async def test_confirm_answers_with_y_and_n(tui, key, default, expected):
    async with tui() as h:
        worker = h.start(h.renderer.confirm("Proceed?", default=default))
        await h.wait_for_screen(ConfirmScreen)
        await h.pilot.press(key)
        assert await worker.wait() is expected
        assert "? Proceed? " + ("yes" if expected else "no") in h.retained_text()


@pytest.mark.parametrize("default", [True, False])
async def test_confirm_enter_takes_the_default(tui, default):
    async with tui() as h:
        worker = h.start(h.renderer.confirm("Proceed?", default=default))
        await h.wait_for_screen(ConfirmScreen)
        await h.pilot.press("enter")
        assert await worker.wait() is default


async def test_confirm_escape_answers_no_even_when_the_default_is_yes(tui):
    async with tui() as h:
        worker = h.start(h.renderer.confirm("Delete everything?", default=True))
        await h.wait_for_screen(ConfirmScreen)
        await h.pilot.press("escape")
        assert await worker.wait() is False
        assert not isinstance(h.app.screen, ConfirmScreen)


async def test_confirm_buttons_are_clickable(tui):
    async with tui() as h:
        worker = h.start(h.renderer.confirm("Proceed?"))
        await h.wait_for_screen(ConfirmScreen)
        await h.pilot.click("#yes")
        assert await worker.wait() is True


# ── select ────────────────────────────────────────────────────────────────


async def test_select_returns_the_highlighted_choice(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices(), title="Pick"))
        await h.wait_for_screen(SelectScreen)
        await h.pilot.press("down", "enter")
        assert await worker.wait() == "b"
        assert "? Pick Beta" in h.retained_text()


async def test_select_skips_disabled_choices(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices()))
        await h.wait_for_screen(SelectScreen)
        await h.pilot.press("down", "down", "enter")  # Beta → (Gamma disabled) → Delta
        assert await worker.wait() == "d"


async def test_select_starts_on_the_initial_value(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices(), initial=["d"]))
        await h.wait_for_screen(SelectScreen)
        await h.pilot.press("enter")
        assert await worker.wait() == "d"


async def test_select_escape_returns_none(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices()))
        await h.wait_for_screen(SelectScreen)
        await h.pilot.press("escape")
        assert await worker.wait() is None
        assert "(none)" in h.retained_text()


async def test_multi_select_toggles_and_submits(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices(), multi=True, initial=["a"]))
        await h.wait_for_screen(MultiSelectScreen)
        await h.pilot.press("down", "space", "enter")
        assert await worker.wait() == ["a", "b"]


async def test_multi_select_refuses_more_than_max_items(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices(), multi=True, initial=["a", "b"], max_items=1))
        screen = await h.wait_for_screen(MultiSelectScreen)
        await h.pilot.press("enter")
        await h.pilot.pause()
        assert h.app.screen is screen  # still open
        assert "at most 1" in str(screen.query_one("#problem").render())
        await h.pilot.press("space", "enter")  # untoggle Alpha (the cursor starts on it)
        assert await worker.wait() == ["b"]


async def test_multi_select_escape_returns_empty(tui):
    async with tui() as h:
        worker = h.start(h.renderer.select(_choices(), multi=True, initial=["a"]))
        await h.wait_for_screen(MultiSelectScreen)
        await h.pilot.press("escape")
        assert await worker.wait() == []


# ── ask ───────────────────────────────────────────────────────────────────


async def test_ask_returns_the_typed_answer(tui):
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("Name")))
        await h.wait_for_screen(PromptScreen)
        await h.pilot.press(*"scout", "enter")
        assert await worker.wait() == "scout"
        assert "? Name scout" in h.retained_text()


async def test_ask_empty_submission_takes_the_default(tui):
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("Model", default="hermes-3")))
        await h.wait_for_screen(PromptScreen)
        await h.pilot.press("enter")
        assert await worker.wait() == "hermes-3"


async def test_ask_reasks_until_the_validator_accepts(tui):
    def must_be_digits(answer: str) -> str | None:
        return None if answer.isdigit() else "Digits only."

    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("Port", validator=must_be_digits)))
        screen = await h.wait_for_screen(PromptScreen)
        await h.pilot.press(*"abc", "enter")
        await h.pilot.pause()
        assert h.app.screen is screen
        assert "Digits only." in str(screen.query_one("#problem").render())
        screen.query_one(Input).value = ""
        await h.pilot.press(*"8080", "enter")
        assert await worker.wait() == "8080"


async def test_ask_escape_returns_the_default(tui):
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("Model", default="hermes-3")))
        await h.wait_for_screen(PromptScreen)
        await h.pilot.press(*"typed", "escape")
        assert await worker.wait() == "hermes-3"


async def test_ask_escape_without_a_default_aborts(tui):
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("Name")))
        await h.wait_for_screen(PromptScreen)
        await h.pilot.press("escape")
        with pytest.raises(WorkerFailed) as failed:
            await worker.wait()
        assert isinstance(failed.value.error, typer.Abort)
        assert not isinstance(h.app.screen, PromptScreen)


async def test_ask_escape_aborts_when_the_validator_rejects_the_default(tui):
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("Port", default="x", validator=lambda a: "no" if a == "x" else None)))
        await h.wait_for_screen(PromptScreen)
        await h.pilot.press("escape")
        with pytest.raises(WorkerFailed) as failed:
            await worker.wait()
        assert isinstance(failed.value.error, typer.Abort)


# ── secrets ───────────────────────────────────────────────────────────────


async def test_secret_ask_is_masked_and_never_retained(tui, caplog):
    caplog.set_level(logging.DEBUG)
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("API key", secret=True)))
        screen = await h.wait_for_screen(PromptScreen)
        field = screen.query_one(Input)
        assert field.password is True
        await h.pilot.press(*SECRET)
        await h.pilot.pause()
        assert SECRET not in str(field.render())  # masked on screen
        await h.pilot.press("enter")
        assert await worker.wait() == SECRET

        assert f"? API key {HIDDEN_ANSWER}" in h.retained_text()
        assert SECRET not in h.retained_text()
        assert SECRET not in h.visible_text()
        assert all(SECRET not in repr(block) for block in h.app.transcript.retained)
    assert SECRET not in caplog.text


async def test_secret_default_is_not_shown_as_a_placeholder(tui):
    async with tui() as h:
        worker = h.start(h.renderer.ask(Question("API key", default=SECRET, secret=True)))
        screen = await h.wait_for_screen(PromptScreen)
        assert screen.query_one(Input).placeholder == ""
        await h.pilot.press("escape")
        assert await worker.wait() == SECRET
        assert SECRET not in h.retained_text()


async def test_secret_is_absent_from_the_exit_replay(monkeypatch, tmp_path):
    monkeypatch.setattr("arcana_cli.tui.app.ARCANA_HOME", tmp_path)
    app = ArcanaApp()
    renderer = TextualRenderer(app)
    answers: list[str] = []

    async def session(pilot: Any) -> None:
        renderer.emit(Text("before"))
        worker = app.run_worker(renderer.ask(Question("API key", secret=True)), exit_on_error=False)
        await wait_for_screen(pilot, PromptScreen)
        await pilot.press(*SECRET, "enter")
        answers.append(await worker.wait())
        renderer.emit(Text("after"))
        await pilot.pause()
        app.exit()

    run_inline_headless(monkeypatch, app, session)
    out = io.StringIO()
    await app.run_inline(console=Console(file=out, width=100, color_system=None))
    assert answers == [SECRET]
    replay = out.getvalue()
    assert "before" in replay and "after" in replay and HIDDEN_ANSWER in replay
    assert SECRET not in replay


# ── worker requirement ────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["ask", "confirm", "select"])
async def test_questions_outside_a_worker_fail_fast(tui, method):
    async with tui() as h:
        calls = {
            "ask": lambda: h.renderer.ask(Question("Name")),
            "confirm": lambda: h.renderer.confirm("Sure?"),
            "select": lambda: h.renderer.select(_choices()),
        }
        with pytest.raises(RuntimeError, match="app worker"):
            await calls[method]()
        assert len(h.app.screen_stack) == 1  # no orphaned dialog


# ── status & stream ───────────────────────────────────────────────────────


async def test_status_shows_in_the_status_bar_while_open(tui):
    async with tui() as h:
        async with h.renderer.status("Thinking"):
            async with h.renderer.status("Recalling memory"):
                assert h.app.status_bar.messages == ("Thinking", "Recalling memory")
                await h.pilot.pause()
                assert "Recalling memory" in str(h.app.status_bar.render_line(0).text)
            assert h.app.status_bar.messages == ("Thinking",)
        assert h.app.status_bar.messages == ()
        assert h.app.status_bar.auto_refresh is None


async def test_status_spinner_animates(tui):
    async with tui() as h:
        async with h.renderer.status("Working"):
            frames = set()
            for _ in range(6):
                await h.pilot.pause(0.1)
                frames.add(h.app.status_bar.render_line(0).text)
            assert len(frames) > 1, "the spinner never redrew"


async def test_status_closes_on_error(tui):
    async with tui() as h:
        with pytest.raises(ValueError):
            async with h.renderer.status("Working"):
                raise ValueError("boom")
        assert h.app.status_bar.messages == ()


async def test_stream_grows_live_then_lands_in_the_transcript(tui):
    async with tui() as h:
        async with h.renderer.stream(prefix="Hermit › ") as sink:
            sink.write("Hello")
            sink.write(", world")
            await h.pilot.pause()
            assert h.app.live.display
            assert h.app.live.content_renderable == Text("Hermit › Hello, world")
        await h.pilot.pause()
        assert not h.app.live.display
        assert "Hermit › Hello, world" in h.retained_text()


async def test_stream_with_a_renderable_prefix(tui):
    async with tui() as h:
        async with h.renderer.stream(prefix=Text("▍")) as sink:
            sink.write("tokens")
        assert "▍tokens" in h.retained_text()


# ── JSON contract ─────────────────────────────────────────────────────────


def test_renderer_for_never_builds_the_textual_adapter():
    assert isinstance(renderer_for(json=True), JsonRenderer)
    assert isinstance(renderer_for(json=False), TtyRenderer)


@pytest.mark.parametrize("module", ["arcana_cli.ui.renderer", "arcana_cli.main"])
def test_the_one_shot_paths_do_not_import_textual(module):
    code = f"import sys, {module}; print('textual' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"
