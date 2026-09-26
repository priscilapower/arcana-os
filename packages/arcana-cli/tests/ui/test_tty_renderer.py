"""Tests for TtyRenderer — Rich console output and line prompts behind the renderer port."""

import asyncio
import io
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

import pytest
import typer
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

import arcana_cli.tui.card_picker as card_picker_app
from arcana.types.card import Card
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_ERROR
from arcana_cli.ui.renderer import Choice, NonInteractiveError, Question, TtyRenderer
from arcana_cli.ui.renderer import tty as tty_mod

if sys.platform != "win32":
    import pty
    import termios


def _renderer() -> tuple[TtyRenderer, io.StringIO]:
    out = io.StringIO()
    return TtyRenderer(Console(file=out, width=100, color_system=None)), out


class _ScriptedPrompt:
    """Stands in for ``typer.prompt``: returns scripted answers, records each call's kwargs."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, text: str, **kwargs: Any) -> str:
        self.calls.append((text, kwargs))
        return self.answers.pop(0)


@pytest.fixture
def prompt(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _ScriptedPrompt]:
    def install(*answers: str) -> _ScriptedPrompt:
        fake = _ScriptedPrompt(*answers)
        monkeypatch.setattr(typer, "prompt", fake)
        return fake

    return install


# ── emit ──────────────────────────────────────────────────────────────────


def test_emit_prints_to_the_console():
    r, out = _renderer()
    r.emit(Text("hello"))
    assert out.getvalue() == "hello\n"


# ── ask ───────────────────────────────────────────────────────────────────


async def test_ask_forwards_prompt_and_default(prompt: Callable[..., _ScriptedPrompt]):
    fake = prompt("gpt-5")
    r, _ = _renderer()
    assert await r.ask(Question("Model", default="gpt")) == "gpt-5"
    assert fake.calls == [("Model", {"default": "gpt", "hide_input": False, "show_default": True})]


async def test_ask_secret_hides_input(prompt: Callable[..., _ScriptedPrompt]):
    fake = prompt("sk-live-123")
    r, _ = _renderer()
    assert await r.ask(Question("API key", secret=True)) == "sk-live-123"
    assert fake.calls[0][1]["hide_input"] is True


async def test_ask_reasks_until_the_validator_accepts(prompt: Callable[..., _ScriptedPrompt]):
    fake = prompt("", "ok")
    r, out = _renderer()
    answer = await r.ask(Question("Name", validator=lambda v: None if v else "Name can't be empty."))
    assert answer == "ok"
    assert len(fake.calls) == 2
    assert "Name can't be empty." in out.getvalue()


async def test_ask_secret_answer_is_never_emitted(prompt: Callable[..., _ScriptedPrompt]):
    prompt("short", "sk-live-123456")
    r, out = _renderer()
    q = Question("API key", secret=True, validator=lambda v: None if len(v) > 8 else "Too short.")
    answer = await r.ask(q)
    assert answer == "sk-live-123456"
    assert "sk-live" not in out.getvalue()
    assert "short" not in out.getvalue().replace("Too short.", "")
    assert "sk-live" not in repr(q)


def test_ask_reads_piped_stdin_through_typer():
    app = typer.Typer()

    @app.command()
    def cmd() -> None:
        print("got", run_async(TtyRenderer().ask(Question("Name"))))

    result = CliRunner().invoke(app, [], input="hermit\n")
    assert result.exit_code == 0
    assert result.output == "Name: hermit\ngot hermit\n"


def test_ask_secret_through_typer_does_not_echo():
    app = typer.Typer()

    @app.command()
    def cmd() -> None:
        answer = run_async(TtyRenderer().ask(Question("Token", secret=True)))
        print("length", len(answer))

    result = CliRunner().invoke(app, [], input="sk-live-123\n")
    assert result.exit_code == 0
    assert "sk-live-123" not in result.output
    assert "length 11" in result.output


def test_ask_secret_default_is_not_shown_in_the_prompt():
    app = typer.Typer()

    @app.command()
    def cmd() -> None:
        answer = run_async(TtyRenderer().ask(Question("API key", default="sk-live-123", secret=True)))
        print("kept" if answer == "sk-live-123" else "changed")

    result = CliRunner().invoke(app, [], input="\n")
    assert result.exit_code == 0
    assert "sk-live-123" not in result.output
    assert "kept" in result.output


def test_ask_secret_empty_default_keeps_its_prompt_shape():
    app = typer.Typer()

    @app.command()
    def cmd() -> None:
        run_async(TtyRenderer().ask(Question("API key", default="", secret=True)))

    result = CliRunner().invoke(app, [], input="\n")
    assert result.output.startswith("API key []: ")


# ── confirm ───────────────────────────────────────────────────────────────


async def test_confirm_forwards_the_default(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, bool]] = []

    def fake_confirm(text: str, *, default: bool) -> bool:
        calls.append((text, default))
        return True

    monkeypatch.setattr(typer, "confirm", fake_confirm)
    r, _ = _renderer()
    assert await r.confirm("Remove it?", default=True) is True
    assert calls == [("Remove it?", True)]


# ── select: choices with previews open the two-pane picker ────────────────


def _previewed(*values: Card, disabled: tuple[Card, ...] = ()) -> list[Choice[Card]]:
    return [Choice(v, v.value, preview=Text(v.value), disabled=v in disabled) for v in values]


@pytest.fixture()
def picker(monkeypatch: pytest.MonkeyPatch) -> Callable[..., dict[str, Any]]:
    """Give the renderer a terminal and stand in for the picker app: it answers with the scripted indexes."""

    def install(answer: list[int]) -> dict[str, Any]:
        seen: dict[str, Any] = {}

        async def fake_pick(choices: Any, **kwargs: Any) -> list[int]:
            seen.update(choices=list(choices), **kwargs)
            return answer

        monkeypatch.setattr(tty_mod, "_is_terminal", lambda: True)
        monkeypatch.setattr(card_picker_app, "pick", fake_pick)
        return seen

    return install


async def test_select_with_previews_opens_the_picker(picker: Callable[..., dict[str, Any]]):
    seen = picker([1])
    r, _ = _renderer()
    choices = _previewed(Card.FOOL, Card.HERMIT)
    assert await r.select(choices, title="Pick", initial=[Card.HERMIT]) is Card.HERMIT
    assert seen["choices"] == choices
    assert (seen["multi"], seen["initial"], seen["title"], seen["max_items"]) == (False, [1], "Pick", None)


async def test_select_multi_with_previews_opens_the_multi_picker(picker: Callable[..., dict[str, Any]]):
    seen = picker([0, 2])
    r, _ = _renderer()
    choices = _previewed(Card.SUN, Card.MOON, Card.STAR)
    picked = await r.select(choices, multi=True, initial=[Card.STAR, Card.SUN], max_items=2)
    assert picked == [Card.SUN, Card.STAR]
    assert seen["multi"] is True
    assert seen["initial"] == [2, 0]  # initial's order: the cursor starts on the first named
    assert seen["max_items"] == 2


async def test_select_picker_cancel(picker: Callable[..., dict[str, Any]]):
    picker([])
    r, _ = _renderer()
    assert await r.select(_previewed(Card.FOOL)) is None
    assert await r.select(_previewed(Card.FOOL), multi=True) == []


async def test_select_picker_leaves_disabled_choices_out(picker: Callable[..., dict[str, Any]]):
    seen = picker([0])
    r, _ = _renderer()
    await r.select(_previewed(Card.FOOL, Card.SUN, disabled=(Card.SUN,)))
    assert [c.value for c in seen["choices"]] == [Card.FOOL]


async def test_select_picker_without_a_terminal_fails_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tty_mod, "_is_terminal", lambda: False)
    err = io.StringIO()
    r = TtyRenderer(Console(file=io.StringIO()), stderr=Console(file=err, width=200, color_system=None))
    with pytest.raises(NonInteractiveError) as exc:
        await r.select(_previewed(Card.FOOL), title="Choose a card", flag="--card")
    assert exc.value.exit_code == EXIT_ERROR
    assert exc.value.flag == "--card"
    assert "'Choose a card' needs an answer, but a non-interactive terminal never prompts; pass --card instead" in (
        err.getvalue()
    )


def test_is_terminal_needs_both_stdin_and_stdout(monkeypatch: pytest.MonkeyPatch):
    class _Stream(io.StringIO):
        def __init__(self, tty: bool) -> None:
            super().__init__()
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    for stdin, stdout, expected in [(True, True, True), (True, False, False), (False, True, False)]:
        monkeypatch.setattr(sys, "stdin", _Stream(stdin))
        monkeypatch.setattr(sys, "stdout", _Stream(stdout))
        assert tty_mod._is_terminal() is expected


# ── select: anything else is a numbered list ──────────────────────────────


async def test_select_numbered_single(prompt: Callable[..., _ScriptedPrompt]):
    prompt("2")
    r, out = _renderer()
    picked = await r.select([Choice("a", "Alpha"), Choice("b", "Beta")], title="Letters")
    assert picked == "b"
    assert "LETTERS" in out.getvalue()
    assert " 1. Alpha" in out.getvalue()
    assert " 2. Beta" in out.getvalue()


async def test_select_numbered_reasks_on_bad_input(prompt: Callable[..., _ScriptedPrompt]):
    fake = prompt("9", "1,2", "x", "1")
    r, out = _renderer()
    assert await r.select([Choice("a", "Alpha"), Choice("b", "Beta")]) == "a"
    assert len(fake.calls) == 4
    assert "Enter numbers between 1 and 2." in out.getvalue()
    assert "Pick one number." in out.getvalue()


async def test_select_numbered_blank_cancels(prompt: Callable[..., _ScriptedPrompt]):
    prompt("", "")
    r, _ = _renderer()
    assert await r.select([Choice("a", "Alpha")]) is None
    assert await r.select([Choice("a", "Alpha")], multi=True) == []


async def test_select_numbered_multi_with_initial_default(prompt: Callable[..., _ScriptedPrompt]):
    fake = prompt("1, 3")
    r, _ = _renderer()
    choices = [Choice("a", "Alpha"), Choice("b", "Beta"), Choice("c", "Gamma")]
    assert await r.select(choices, multi=True, initial=["b", "c"]) == ["a", "c"]
    assert fake.calls[0][1]["default"] == "2, 3"


async def test_select_numbered_enforces_max_items(prompt: Callable[..., _ScriptedPrompt]):
    prompt("1,2", "2")
    r, out = _renderer()
    assert await r.select([Choice("a", "A"), Choice("b", "B")], multi=True, max_items=1) == ["b"]
    assert "Pick at most 1." in out.getvalue()


async def test_select_numbered_leaves_disabled_out(prompt: Callable[..., _ScriptedPrompt]):
    prompt("1")
    r, out = _renderer()
    assert await r.select([Choice("a", "Alpha", disabled=True), Choice("b", "Beta")]) == "b"
    assert "Alpha" not in out.getvalue()


async def test_select_numbered_labels_are_not_markup(prompt: Callable[..., _ScriptedPrompt]):
    prompt("1")
    r, out = _renderer()
    await r.select([Choice("a", "[bold]literal[/bold]")])
    assert "[bold]literal[/bold]" in out.getvalue()


# ── status / stream ───────────────────────────────────────────────────────


async def test_status_wraps_the_block():
    r, _ = _renderer()
    ran = False
    async with r.status("thinking"):
        ran = True
    assert ran


def _split_renderer(*, stderr_terminal: bool) -> tuple[TtyRenderer, io.StringIO, io.StringIO]:
    out, err_out = io.StringIO(), io.StringIO()
    stderr = Console(file=err_out, width=100, force_terminal=stderr_terminal, color_system=None)
    return TtyRenderer(Console(file=out, width=100, color_system=None), stderr=stderr), out, err_out


def test_note_goes_to_stderr():
    r, out, err_out = _split_renderer(stderr_terminal=False)
    r.note("routed to scout")
    assert out.getvalue() == ""
    assert err_out.getvalue() == "routed to scout\n"


async def test_status_spins_on_stderr_never_stdout():
    r, out, err_out = _split_renderer(stderr_terminal=True)
    async with r.status("thinking"):
        await asyncio.sleep(0.05)
    assert out.getvalue() == ""
    assert "thinking" in err_out.getvalue()


async def test_status_draws_nothing_when_stderr_is_not_a_terminal():
    r, out, err_out = _split_renderer(stderr_terminal=False)
    async with r.status("thinking"):
        await asyncio.sleep(0.05)
    assert (out.getvalue(), err_out.getvalue()) == ("", "")


async def test_status_stop_ends_the_spinner_before_the_block_does():
    r, _, err_out = _split_renderer(stderr_terminal=True)
    async with r.status("thinking") as status:
        status.stop()
        drawn = err_out.getvalue()
        await asyncio.sleep(0.05)
        assert err_out.getvalue() == drawn, "the spinner kept drawing after stop()"
        status.stop()  # a second stop is a no-op


async def test_stream_writes_chunks_then_a_newline():
    r, out = _renderer()
    async with r.stream(prefix=Text("» ")) as sink:
        sink.write("hello ")
        sink.write("world")
    assert out.getvalue() == "» hello world\n"


async def test_stream_writes_raw_chunks_even_with_a_render():
    r, out = _renderer()
    async with r.stream(render=lambda t: Text(t.upper())) as sink:
        sink.write("hello")
    assert out.getvalue() == "hello\n"


# ── blocking reads off the loop ───────────────────────────────────────────


async def test_off_loop_keeps_the_loop_running():
    release = threading.Event()
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while not release.is_set():
            ticks += 1
            await asyncio.sleep(0.001)

    def blocking_read() -> str:
        release.wait(timeout=5)
        return "done"

    tick_task = asyncio.create_task(ticker())
    read = asyncio.create_task(tty_mod._off_loop(blocking_read))
    await asyncio.sleep(0.05)
    release.set()
    assert await read == "done"
    await tick_task
    assert ticks > 1


async def test_off_loop_reraises_the_reads_error():
    def boom() -> str:
        raise ValueError("bad read")

    with pytest.raises(ValueError, match="bad read"):
        await tty_mod._off_loop(boom)


async def test_off_loop_cancel_is_a_prompt_abort(capsys: pytest.CaptureFixture[str]):
    """Ctrl+C cancels the awaiting task; it surfaces as typer's Abort, like typer.prompt."""
    never = threading.Event()
    read = asyncio.create_task(tty_mod._off_loop(lambda: never.wait(timeout=5), hidden=True))
    await asyncio.sleep(0.01)
    read.cancel()
    with pytest.raises(typer.Abort):
        await read
    never.set()
    assert capsys.readouterr().out == "\n"  # the line break a hidden read leaves missing


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal attributes")
def test_terminal_restored_puts_echo_back(monkeypatch: pytest.MonkeyPatch):
    master, slave = pty.openpty()
    try:
        with os.fdopen(slave, "r", closefd=False) as tty_in:
            monkeypatch.setattr(sys, "stdin", tty_in)
            with tty_mod._terminal_restored():
                attrs = termios.tcgetattr(slave)
                attrs[3] &= ~termios.ECHO
                termios.tcsetattr(slave, termios.TCSANOW, attrs)
            assert termios.tcgetattr(slave)[3] & termios.ECHO
    finally:
        os.close(master)
        os.close(slave)
