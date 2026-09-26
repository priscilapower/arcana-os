"""``TextualRenderer`` — the renderer port over a running :class:`~arcana_cli.tui.app.ArcanaApp`.

Output is appended to the app's transcript; questions are modal dialogs awaited
with ``push_screen_wait`` (a selection whose choices carry previews gets the
two-pane :class:`~arcana_cli.tui.card_picker.CardPickerScreen`); a status is the status-bar spinner; a stream grows in
the live block and lands in the transcript when it closes. A note is a
transcript block like any other: the session has no separate stream to put it on.

A question can only be awaited from inside an app worker (``app.run_worker``):
Textual's ``push_screen_wait`` needs one, and each prompt method checks for it
up front with a clear error instead of hanging.

Every dialog is cancellable with Esc, and cancelling the worker awaiting it
(Ctrl+C on a turn) takes the dialog down with it. A cancelled :meth:`confirm` answers no and
a cancelled :meth:`select` picks nothing. A cancelled :meth:`ask` raises
:class:`typer.Abort`, the same cancellation the line-prompt adapter surfaces on
Ctrl+C, so Esc ends a wizard rather than walking it on through its defaults (an
empty Enter is how a default is taken).

A wait (:meth:`TextualRenderer.waiting`) is a :class:`~arcana_cli.tui.screens.WaitScreen`
over the session showing what the user has to do; Esc on it raises
:class:`typer.Abort` out of the waiting block, and nothing it showed reaches
the transcript.

Each answered question is echoed into the transcript as a one-line record. A
secret answer never is: its record says ``(hidden)``, so the answer reaches the
caller and nowhere else — not the transcript, not the exit replay.

Not re-exported from :mod:`arcana_cli.ui.renderer`: importing it loads Textual,
which the one-shot commands (and every ``--json`` path) never need.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Literal, TypeVar, overload

import typer
from pydantic import BaseModel
from rich.console import RenderableType
from rich.pretty import Pretty
from rich.text import Text
from textual.screen import Screen
from textual.worker import NoActiveWorker, get_current_worker  # pyright: ignore[reportUnknownVariableType]

from arcana_cli.tui.app import ArcanaApp
from arcana_cli.tui.card_picker import CardPickerScreen
from arcana_cli.tui.screens import ConfirmScreen, MultiSelectScreen, PromptScreen, SelectScreen, WaitScreen
from arcana_cli.ui.renderer.port import (
    Choice,
    Emittable,
    Question,
    StatusHandle,
    StreamRender,
    StreamSink,
    WaitHandle,
    initial_indexes,
)
from arcana_cli.ui.renderer.presentable import Failure, Presentable
from arcana_cli.ui.theme import ACCENT, TXT2

T = TypeVar("T")
_A = TypeVar("_A")

#: What the transcript record shows in place of a secret answer.
HIDDEN_ANSWER = "(hidden)"


def _require_worker(method: str) -> None:
    """Fail fast when a question is asked outside an app worker, where it could never be answered.

    Checked before the dialog is pushed: Textual's own check runs after the push,
    which would leave an unanswerable dialog on screen. (The import of
    ``get_current_worker`` carries a pyright ignore because Textual annotates it
    ``-> Worker`` with the type parameter left open; only its raising is used.)
    """
    try:
        get_current_worker()
    except NoActiveWorker:
        raise RuntimeError(
            f"TextualRenderer.{method} must be awaited inside an app worker (app.run_worker): "
            "its dialog is awaited with push_screen_wait"
        ) from None


async def _wait_for_answer(app: ArcanaApp, screen: Screen[_A]) -> _A:
    """Push ``screen`` and await its dismiss value; a cancelled wait takes the dialog down with it.

    Cancelling the awaiting worker (Ctrl+C on a turn) would otherwise leave an
    unanswerable dialog on screen.
    """
    try:
        return await app.push_screen_wait(screen)
    except asyncio.CancelledError:
        if app.screen is screen:
            app.pop_screen()
        raise


def _record(prompt: str, answer: str) -> Text:
    """The transcript line recording an answered question."""
    return Text.assemble(("? ", f"bold {ACCENT}"), prompt, " ", (answer, TXT2))


class _LiveSink:
    """Feeds stream chunks into the app's live block."""

    def __init__(self, app: ArcanaApp) -> None:
        self._app = app

    def write(self, chunk: str) -> None:
        self._app.live.feed(chunk)


class _StatusBarEntry:
    """Stops one status-bar entry."""

    def __init__(self, app: ArcanaApp, handle: int) -> None:
        self._app = app
        self._handle = handle

    def stop(self) -> None:
        self._app.status_bar.pop(self._handle)


class TextualRenderer:
    """Renders into a running :class:`~arcana_cli.tui.app.ArcanaApp`."""

    def __init__(self, app: ArcanaApp) -> None:
        self._app = app

    def emit(self, renderable: Emittable) -> None:
        """Append one block to the transcript.

        A :class:`~arcana_cli.ui.renderer.presentable.Presentable` shows its
        human view. A string is Rich markup, as it is for ``console.print`` (so
        ``ok(...)`` / ``err(...)`` render the same on every surface); JSON data
        is pretty-printed.
        """
        if isinstance(renderable, Presentable):
            renderable = renderable.to_rich()
        if isinstance(renderable, str):
            renderable = Text.from_markup(renderable)
        elif isinstance(renderable, BaseModel):
            renderable = Pretty(renderable.model_dump(mode="json"))
        elif isinstance(renderable, dict | list):
            renderable = Pretty(renderable)
        self._app.transcript.append(renderable)

    def note(self, renderable: RenderableType) -> None:
        self.emit(renderable)

    def error(self, failure: Failure) -> None:
        self.emit(failure.to_rich())

    async def ask(self, q: Question) -> str:
        _require_worker("ask")
        answer = await _wait_for_answer(self._app, PromptScreen(q))
        if answer is None:
            raise typer.Abort()
        self._app.transcript.append(_record(q.prompt, HIDDEN_ANSWER if q.secret else answer))
        return answer

    async def confirm(self, text: str, *, default: bool = False, flag: str | None = None) -> bool:
        _require_worker("confirm")
        answer = await _wait_for_answer(self._app, ConfirmScreen(text, default=default))
        self._app.transcript.append(_record(text, "yes" if answer else "no"))
        return answer

    @overload
    async def select(
        self,
        choices: Sequence[Choice[T]],
        *,
        multi: Literal[False] = False,
        initial: Sequence[T] = (),
        title: str = "",
        max_items: int | None = None,
        flag: str | None = None,
    ) -> T | None: ...

    @overload
    async def select(
        self,
        choices: Sequence[Choice[T]],
        *,
        multi: Literal[True],
        initial: Sequence[T] = (),
        title: str = "",
        max_items: int | None = None,
        flag: str | None = None,
    ) -> list[T]: ...

    async def select(
        self,
        choices: Sequence[Choice[T]],
        *,
        multi: bool = False,
        initial: Sequence[T] = (),
        title: str = "",
        max_items: int | None = None,
        flag: str | None = None,
    ) -> T | list[T] | None:
        _require_worker("select")
        seeds = initial_indexes(choices, initial)
        picked: list[int]
        if any(c.preview is not None for c in choices if not c.disabled):
            chosen = await _wait_for_answer(
                self._app,
                CardPickerScreen(choices, multi=multi, initial=seeds, title=title, max_items=max_items),
            )
            picked = chosen if chosen is not None else []
        elif multi:
            many = await _wait_for_answer(
                self._app, MultiSelectScreen(choices, initial=seeds, title=title, max_items=max_items)
            )
            picked = many if many is not None else []
        else:
            one = await _wait_for_answer(
                self._app, SelectScreen(choices, initial=seeds[0] if seeds else None, title=title)
            )
            picked = [one] if one is not None else []
        labels = ", ".join(choices[i].label for i in picked) or "(none)"
        self._app.transcript.append(_record(title or "Select", labels))
        values = [choices[i].value for i in picked]
        if multi:
            return values
        return values[0] if values else None

    @asynccontextmanager
    async def status(self, msg: str) -> AsyncGenerator[StatusHandle]:
        entry = _StatusBarEntry(self._app, self._app.status_bar.push(msg))
        try:
            yield entry
        finally:
            entry.stop()

    @asynccontextmanager
    async def stream(
        self, prefix: RenderableType | None = None, *, render: StreamRender | None = None
    ) -> AsyncGenerator[StreamSink]:
        self._app.live.open(prefix, render)
        try:
            yield _LiveSink(self._app)
        finally:
            self._app.transcript.append(self._app.live.close())

    @asynccontextmanager
    async def waiting(self, msg: str, *, title: str = "") -> AsyncGenerator[WaitHandle]:
        """Show a :class:`~arcana_cli.tui.screens.WaitScreen` for the block; Esc on it raises :class:`typer.Abort`.

        Esc cancels the task running the block, so whatever it awaits (a
        loopback listener, a polling loop) unwinds through its own cleanup; the
        cancellation is then turned into the abort. Any other cancellation (Ctrl+C
        on the worker) passes through untouched. Either way the dialog comes down.
        """
        _require_worker("waiting")
        task = asyncio.current_task()
        if task is None:  # a worker always runs as a task; this narrows the type
            raise RuntimeError("TextualRenderer.waiting needs a running task")
        # Esc only cancels while the task is suspended inside the block, so the
        # cancellation always lands there, never on whatever runs after it.
        inside = False
        called_off = False

        def call_off() -> None:
            nonlocal called_off
            if inside and not called_off:
                called_off = True
                task.cancel()

        screen = WaitScreen(msg, title=title, on_cancel=call_off)
        try:
            # Inside the try, so a cancellation that lands while the dialog is
            # still being pushed takes it down too.
            await self._app.push_screen(screen)
            inside = True
            yield screen
        except asyncio.CancelledError:
            if not called_off:
                raise
            task.uncancel()
            raise typer.Abort() from None
        finally:
            inside = False
            if self._app.screen is screen:
                self._app.pop_screen()
        if called_off:  # the block swallowed the cancellation; the wait was still called off
            task.uncancel()
            raise typer.Abort()
