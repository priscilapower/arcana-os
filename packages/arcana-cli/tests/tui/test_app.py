"""Tests for ArcanaApp: composition, driver and mouse selection, and the exit replay."""

import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from rich.table import Table
from rich.text import Text

from arcana_cli.tui import app as app_mod
from arcana_cli.tui.app import FULLSCREEN_NOTICE, ArcanaApp, replay_tail
from arcana_cli.tui.config import UiConfig, load_ui_config
from arcana_cli.tui.theme_tcss import ARCANA_THEME
from arcana_cli.tui.widgets import LiveBlock, StatusBar, Transcript
from arcana_cli.ui.theme import ACCENT, SURFACE
from tests.support.tui import run_inline_headless


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=100, color_system=None), out


class _RunSpy:
    """Stands in for ``App.run_async``: records its kwargs, lets a test write to the transcript, returns."""

    def __init__(self, app: ArcanaApp, *blocks: Any) -> None:
        self.app = app
        self.blocks = blocks
        self.kwargs: dict[str, Any] = {}

    async def __call__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.app.transcript.retained.extend(self.blocks)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(app_mod, "ARCANA_HOME", tmp_path)
    return tmp_path


# ── composition ───────────────────────────────────────────────────────────


async def test_app_mounts_transcript_live_block_and_status_bar(tui):
    async with tui() as h:
        assert isinstance(h.app.query_one("#transcript"), Transcript)
        assert isinstance(h.app.query_one("#live"), LiveBlock)
        assert isinstance(h.app.query_one("#status"), StatusBar)
        assert not h.app.live.display  # hidden until a stream opens


async def test_app_uses_the_generated_theme(tui):
    async with tui() as h:
        assert h.app.theme == ARCANA_THEME.name
        variables = h.app.get_css_variables()
        assert variables["accent"] == ACCENT
        assert variables["surface"] == SURFACE
        assert h.app.screen.styles.background.hex.lower() == SURFACE


async def test_transcript_renders_a_rich_table(tui):
    async with tui() as h:
        table = Table("Card", "Temp")
        table.add_row("The Hermit", "0.4")
        h.renderer.emit(table)
        await h.pilot.pause()
        assert "The Hermit" in h.visible_text()
        assert h.app.transcript.retained == [table]


# ── driver & mouse ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("platform", "inline"), [("darwin", True), ("linux", True), ("win32", False)])
async def test_run_inline_picks_the_driver_by_platform(monkeypatch, home, platform, inline):
    monkeypatch.setattr(sys, "platform", platform)
    app = ArcanaApp()
    spy = _RunSpy(app)
    monkeypatch.setattr(app, "run_async", spy)
    await app.run_inline(console=_console()[0])
    assert spy.kwargs["inline"] is inline


async def test_mouse_defaults_on(monkeypatch, home):
    app = ArcanaApp()
    spy = _RunSpy(app)
    monkeypatch.setattr(app, "run_async", spy)
    await app.run_inline(console=_console()[0])
    assert spy.kwargs["mouse"] is True


async def test_mouse_follows_the_ui_config(monkeypatch, home):
    (home / "config.json").write_text(json.dumps({"version": "0.1.0", "ui": {"mouse": False}}))
    app = ArcanaApp()
    spy = _RunSpy(app)
    monkeypatch.setattr(app, "run_async", spy)
    await app.run_inline(console=_console()[0])
    assert spy.kwargs["mouse"] is False


async def test_explicit_mouse_overrides_the_config(monkeypatch, home):
    (home / "config.json").write_text(json.dumps({"ui": {"mouse": True}}))
    app = ArcanaApp()
    spy = _RunSpy(app)
    monkeypatch.setattr(app, "run_async", spy)
    await app.run_inline(mouse=False, console=_console()[0])
    assert spy.kwargs["mouse"] is False


@pytest.mark.parametrize(
    "content",
    [None, "not json", json.dumps([1, 2]), json.dumps({"ui": None}), json.dumps({"ui": {"mouse": "maybe"}})],
)
def test_ui_config_falls_back_to_defaults(tmp_path, content):
    if content is not None:
        (tmp_path / "config.json").write_text(content)
    assert load_ui_config(tmp_path) == UiConfig()


def test_ui_config_ignores_unrelated_keys(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"memory": {"enabled": True}, "ui": {"mouse": False, "x": 1}}))
    assert load_ui_config(tmp_path).mouse is False


# ── Windows notice ────────────────────────────────────────────────────────


async def test_windows_notice_shows_on_the_first_run_only(monkeypatch, home):
    monkeypatch.setattr(sys, "platform", "win32")
    for expected in (True, False):
        app = ArcanaApp()
        monkeypatch.setattr(app, "run_async", _RunSpy(app))
        console, out = _console()
        await app.run_inline(console=console)
        assert (FULLSCREEN_NOTICE in out.getvalue()) is expected


async def test_windows_notice_shows_every_time_without_a_home(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "ARCANA_HOME", tmp_path / "missing")
    monkeypatch.setattr(sys, "platform", "win32")
    for _ in range(2):
        app = ArcanaApp()
        monkeypatch.setattr(app, "run_async", _RunSpy(app))
        console, out = _console()
        await app.run_inline(console=console)
        assert FULLSCREEN_NOTICE in out.getvalue()


async def test_no_notice_on_posix(monkeypatch, home):
    monkeypatch.setattr(sys, "platform", "linux")
    app = ArcanaApp()
    monkeypatch.setattr(app, "run_async", _RunSpy(app))
    console, out = _console()
    await app.run_inline(console=console)
    assert out.getvalue() == ""


# ── exit replay ───────────────────────────────────────────────────────────


async def test_exit_replays_the_transcript_to_the_console(monkeypatch, home):
    app = ArcanaApp()
    monkeypatch.setattr(app, "run_async", _RunSpy(app, Text("first block"), Text("second block")))
    console, out = _console()
    await app.run_inline(console=console)
    assert out.getvalue() == "first block\nsecond block\n"


async def test_exit_replay_is_capped_with_a_note(monkeypatch, home):
    app = ArcanaApp()
    monkeypatch.setattr(app, "run_async", _RunSpy(app, *(Text(f"block {i}") for i in range(5))))
    console, out = _console()
    await app.run_inline(console=console, replay_limit=2)
    assert out.getvalue() == "… 3 earlier blocks not shown\nblock 3\nblock 4\n"


async def test_exit_replay_runs_even_when_the_app_fails(monkeypatch, home):
    app = ArcanaApp()

    async def boom(**kwargs: Any) -> None:
        app.transcript.retained.append(Text("before the crash"))
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "run_async", boom)
    console, out = _console()
    with pytest.raises(RuntimeError):
        await app.run_inline(console=console)
    assert "before the crash" in out.getvalue()


async def test_run_inline_restores_the_loops_task_factory(monkeypatch, home):
    loop = asyncio.get_running_loop()
    before = loop.get_task_factory()
    app = ArcanaApp()

    async def quit_soon(pilot: Any) -> None:
        app.exit()

    # A real (headless) run: Textual installs its eager task factory while running.
    run_inline_headless(monkeypatch, app, quit_soon)
    await app.run_inline(console=_console()[0])
    assert loop.get_task_factory() is before


def test_replay_tail_singular_note():
    blocks: list[Any] = [Text("a"), Text("b")]
    tail = replay_tail(blocks, 1)
    assert isinstance(tail[0], Text) and tail[0].plain == "… 1 earlier block not shown"
    assert tail[1:] == blocks[1:]


def test_replay_tail_keeps_everything_under_the_cap():
    blocks: list[Any] = [Text("a"), Text("b")]
    assert replay_tail(blocks, 10) == blocks
