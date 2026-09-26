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

from arcana.types.card import Card
from arcana_cli._async import run_async
from arcana_cli.ui.renderer import Choice, Question, TtyRenderer
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


# ── select: card choices open the card picker ─────────────────────────────


async def test_select_cards_opens_the_single_picker(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def fake_select_card(prompt: str, *, initial: Card | None, exclude: set[Card]) -> Card | None:
        seen.update(prompt=prompt, initial=initial, exclude=exclude)
        return Card.HERMIT

    monkeypatch.setattr(tty_mod, "select_card", fake_select_card)
    r, _ = _renderer()
    choices = [Choice(c, c.value) for c in (Card.FOOL, Card.HERMIT)]
    picked = await r.select(choices, title="Pick", initial=[Card.HERMIT])
    assert picked is Card.HERMIT
    assert seen["prompt"] == "Pick"
    assert seen["initial"] is Card.HERMIT
    assert seen["exclude"] == set(Card) - {Card.FOOL, Card.HERMIT}


async def test_select_cards_multi_opens_the_multi_picker(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def fake_select_cards(
        prompt: str, *, initial: list[Card], max_items: int | None, exclude: set[Card]
    ) -> list[Card]:
        seen.update(prompt=prompt, initial=initial, max_items=max_items, exclude=exclude)
        return [Card.SUN, Card.MOON]

    monkeypatch.setattr(tty_mod, "select_cards", fake_select_cards)
    r, _ = _renderer()
    choices = [Choice(c, c.value) for c in Card if c is not Card.WORLD]
    picked = await r.select(choices, multi=True, initial=[Card.SUN], max_items=2)
    assert picked == [Card.SUN, Card.MOON]
    assert seen == {"prompt": "Select cards", "initial": [Card.SUN], "max_items": 2, "exclude": {Card.WORLD}}


async def test_select_cards_cancel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tty_mod, "select_card", lambda *a, **k: None)
    r, _ = _renderer()
    assert await r.select([Choice(Card.FOOL, "fool")]) is None


async def test_select_cards_leaves_disabled_cards_out(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def fake_select_card(prompt: str, *, initial: Card | None, exclude: set[Card]) -> Card | None:
        seen["exclude"] = exclude
        return Card.FOOL

    monkeypatch.setattr(tty_mod, "select_card", fake_select_card)
    r, _ = _renderer()
    await r.select([Choice(Card.FOOL, "fool"), Choice(Card.SUN, "sun", disabled=True)])
    assert Card.SUN in seen["exclude"]


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


async def test_stream_writes_chunks_then_a_newline():
    r, out = _renderer()
    async with r.stream(prefix=Text("» ")) as sink:
        sink.write("hello ")
        sink.write("world")
    assert out.getvalue() == "» hello world\n"


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
