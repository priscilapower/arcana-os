"""The Pilot harness for the interactive app.

:func:`arcana_pilot` runs a headless :class:`~arcana_cli.tui.app.ArcanaApp` under
Textual's ``run_test`` and hands back a :class:`TuiHarness`: the app, its
``Pilot`` for key presses and clicks, and a
:class:`~arcana_cli.ui.renderer.textual_renderer.TextualRenderer` bound to it.

A renderer question must be awaited from an app worker, so a test starts the
command coroutine with :meth:`TuiHarness.start`, drives the dialog with the
pilot, then collects the result::

    async with arcana_pilot() as tui:
        worker = tui.start(tui.renderer.confirm("Delete?"))
        await tui.wait_for_screen(ConfirmScreen)
        await tui.pilot.press("y")
        assert await worker.wait() is True
"""

import io
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import pytest
from rich.console import Console
from textual.pilot import Pilot
from textual.screen import Screen
from textual.worker import Worker

from arcana_cli.tui.app import ArcanaApp
from arcana_cli.ui.renderer.textual_renderer import TextualRenderer

T = TypeVar("T")

#: Headless terminal size for app tests (columns, rows).
DEFAULT_SIZE = (100, 30)


class TuiHarness:
    """A running headless app, its pilot, and a renderer bound to it."""

    def __init__(self, app: ArcanaApp, pilot: Pilot[None]) -> None:
        self.app = app
        self.pilot = pilot
        self.renderer = TextualRenderer(app)

    def start(self, coro: Coroutine[Any, Any, T]) -> Worker[T]:
        """Run ``coro`` in an app worker; failures surface from ``await worker.wait()``, not as an app crash."""
        return self.app.run_worker(coro, exit_on_error=False)

    async def wait_for_screen(self, screen_type: type[Screen[Any]]) -> Screen[Any]:
        """Let the app settle until a ``screen_type`` dialog is on top, and return it."""
        return await wait_for_screen(self.pilot, screen_type)

    def visible_text(self) -> str:
        """The transcript lines as currently rendered in the widget (plain text)."""
        return "\n".join(strip.text for strip in self.app.transcript.lines)

    def retained_text(self, width: int = 100) -> str:
        """Every retained transcript block, rendered as plain text."""
        console = Console(width=width, record=True, file=io.StringIO(), color_system=None)
        for block in self.app.transcript.retained:
            console.print(block)
        return console.export_text()


async def wait_for_screen(pilot: Pilot[Any], screen_type: type[Screen[Any]]) -> Screen[Any]:
    """Let ``pilot``'s app settle until a ``screen_type`` dialog is on top, and return it."""
    for _ in range(50):
        await pilot.pause()
        if isinstance(pilot.app.screen, screen_type):
            return pilot.app.screen
    raise AssertionError(f"{screen_type.__name__} never opened; top screen is {pilot.app.screen!r}")


def run_inline_headless(
    monkeypatch: pytest.MonkeyPatch, app: ArcanaApp, session: Callable[[Pilot[Any]], Awaitable[None]]
) -> None:
    """Make ``app.run_inline()`` run the real app headless, driven by ``session``.

    Everything around the run (driver and mouse choice, the task-factory restore,
    the exit replay) is the real code; only the terminal is swapped for Textual's
    headless driver. ``session`` must end with ``app.exit()``.
    """
    original = app.run_async

    async def headless(**kwargs: Any) -> None:
        await original(headless=True, auto_pilot=session, mouse=kwargs["mouse"])

    monkeypatch.setattr(app, "run_async", headless)


@asynccontextmanager
async def arcana_pilot(size: tuple[int, int] = DEFAULT_SIZE) -> AsyncIterator[TuiHarness]:
    """Run a fresh :class:`ArcanaApp` headless at ``size`` for the duration of the block."""
    app = ArcanaApp()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        yield TuiHarness(app, pilot)
