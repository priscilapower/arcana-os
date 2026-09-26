"""``TtyRenderer`` — the renderer port over a Rich console and line prompts.

Output is ``console.print``; questions are ``typer.prompt`` / ``typer.confirm``.
That is exactly what the commands did before they took a renderer, so a
converted command's output is unchanged byte for byte.

A selection whose choices carry previews (cards, agents) opens the two-pane
picker (:mod:`arcana_cli.tui.card_picker`) as a short-lived Textual app on the
invocation's own event loop; it needs a terminal, so without one it fails closed
with :class:`~arcana_cli.ui.renderer.port.NonInteractiveError` naming the flag
that answers it. Any other selection is a numbered list read as a line.

Every blocking line read runs on a daemon thread (see :func:`_off_loop`), never
on the event loop, so anything else scheduled on the loop keeps running while
the user types.
"""

import asyncio
import contextlib
import sys
import threading
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from typing import IO, Any, Literal, TypeVar, overload

import typer
from rich.console import Console, RenderableType
from rich.text import Text

from arcana_cli.ui.renderer.port import (
    Choice,
    JsonAble,
    Question,
    StreamRender,
    StreamSink,
    initial_indexes,
    refuse,
)
from arcana_cli.ui.theme import err, eyebrow

if sys.platform != "win32":
    import termios

T = TypeVar("T")
_R = TypeVar("_R")

#: How :class:`NonInteractiveError` names this surface when it has no terminal to prompt on.
NO_TERMINAL = "a non-interactive terminal"


def _is_terminal() -> bool:
    """Whether stdin and stdout are both a terminal, which the two-pane picker draws on and reads from."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # a closed or replaced stream
        return False


def _settle_result(future: asyncio.Future[_R], result: _R) -> None:
    if not future.cancelled():
        future.set_result(result)


def _settle_exception(future: asyncio.Future[_R], exc: BaseException) -> None:
    if not future.cancelled():
        future.set_exception(exc)


@contextmanager
def _terminal_restored() -> Generator[None]:
    """Put stdin's terminal attributes back as they were when the block exits.

    A hidden-input prompt switches echo off and restores it itself — unless the
    wait is abandoned (Ctrl+C cancels the awaiting task while the read thread is
    still blocked), in which case the process would exit with echo still off.
    Restoring here covers that path; on the normal path it is a no-op.
    """
    saved: list[Any] | None = None
    fd = -1
    if sys.platform != "win32":
        with contextlib.suppress(OSError, ValueError):  # not a real tty (pipes, test runners)
            fd = sys.stdin.fileno()
            if sys.stdin.isatty():
                saved = termios.tcgetattr(fd)
    try:
        yield
    finally:
        if sys.platform != "win32" and saved is not None:
            with contextlib.suppress(OSError, termios.error):
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)


async def _off_loop(fn: Callable[[], _R], *, hidden: bool = False) -> _R:
    """Run the blocking read ``fn`` on a daemon thread and await its result.

    Not :func:`asyncio.to_thread`: the default executor's threads are joined when
    the loop shuts down, so a Ctrl+C during a prompt would hang the process until
    the user pressed Enter. A daemon thread is simply abandoned instead — safe
    because an aborted prompt ends the one-shot command, so nothing is left to
    read stdin after it.

    Ctrl+C reaches the loop (which cancels the awaiting task), not the read
    thread, so the cancellation is surfaced the way ``typer.prompt`` surfaces
    Ctrl+C: :class:`typer.Abort` — "Aborted." and exit 1 — with the line break
    a ``hidden`` read would otherwise leave missing.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_R] = loop.create_future()

    def work() -> None:
        try:
            result = fn()
        except BaseException as exc:  # handed to the awaiting task, which re-raises it
            with contextlib.suppress(RuntimeError):  # loop already closed after a cancel
                loop.call_soon_threadsafe(_settle_exception, future, exc)
        else:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_settle_result, future, result)

    with _terminal_restored():
        threading.Thread(target=work, name="arcana-prompt", daemon=True).start()
        try:
            return await future
        except asyncio.CancelledError:
            if hidden:
                typer.echo()
            raise typer.Abort() from None


def _parse_numbers(raw: str, count: int) -> list[int] | None:
    """Parse ``"1, 3"`` into zero-based indexes below ``count``; ``None`` if any part is invalid."""
    picked: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= count:
            return None
        if (index := int(part) - 1) not in picked:
            picked.append(index)
    return picked


class _FileSink:
    """Writes stream chunks straight to the console's file, flushed per chunk."""

    def __init__(self, file: IO[str]) -> None:
        self._file = file

    def write(self, chunk: str) -> None:
        self._file.write(chunk)
        self._file.flush()


class TtyRenderer:
    """Renders to a Rich :class:`~rich.console.Console` and asks with line prompts."""

    def __init__(self, console: Console | None = None, *, stderr: Console | None = None) -> None:
        self._console = console if console is not None else Console()
        self._stderr = stderr if stderr is not None else Console(stderr=True)

    def emit(self, renderable: RenderableType | JsonAble) -> None:
        self._console.print(renderable)

    async def ask(self, q: Question) -> str:
        # A secret's default is never shown in the prompt line (an empty one still
        # renders as ``[]``, as it always has).
        show_default = not (q.secret and q.default)
        while True:
            answer = await _off_loop(
                lambda: str(typer.prompt(q.prompt, default=q.default, hide_input=q.secret, show_default=show_default)),
                hidden=q.secret,
            )
            problem = q.validator(answer) if q.validator is not None else None
            if problem is None:
                return answer
            self._console.print(err(problem))

    async def confirm(self, text: str, *, default: bool = False, flag: str | None = None) -> bool:
        return await _off_loop(lambda: bool(typer.confirm(text, default=default)))

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
        """Choices with previews open the two-pane picker; anything else is a numbered list.

        Disabled choices are left out. The picker needs a terminal: without one
        it raises :class:`NonInteractiveError` (message on stderr) naming ``flag``.
        """
        enabled = [c for c in choices if not c.disabled]
        if any(c.preview is not None for c in enabled):
            picked = await self._select_picker(
                enabled, multi=multi, initial=initial, title=title, max_items=max_items, flag=flag
            )
        else:
            picked = await self._select_numbered(
                enabled, multi=multi, initial=initial, title=title, max_items=max_items
            )
        if multi:
            return picked
        return picked[0] if picked else None

    async def _select_picker(
        self,
        choices: Sequence[Choice[T]],
        *,
        multi: bool,
        initial: Sequence[T],
        title: str,
        max_items: int | None,
        flag: str | None,
    ) -> list[T]:
        if not _is_terminal():
            refuse(self._stderr, title or "a selection", flag=flag, surface=NO_TERMINAL)
        # Deferred: the picker is a Textual app (over 100 ms to import), which the
        # one-shot and --json commands that never open it must not pay for; a test
        # asserts this module imports without Textual.
        from arcana_cli.tui.card_picker import pick

        picked = await pick(
            choices, multi=multi, initial=initial_indexes(choices, initial), title=title, max_items=max_items
        )
        return [choices[i].value for i in picked]

    async def _select_numbered(
        self,
        choices: Sequence[Choice[T]],
        *,
        multi: bool,
        initial: Sequence[T],
        title: str,
        max_items: int | None,
    ) -> list[T]:
        if title:
            self._console.print(eyebrow(title))
        for number, choice in enumerate(choices, 1):
            self._console.print(Text(f"  {number:2}. {choice.label}"))
        default = ", ".join(str(n) for n, c in enumerate(choices, 1) if c.value in initial)
        hint = "comma-separated #s, blank for none" if multi else "#, blank to cancel"
        while True:
            raw = await _off_loop(
                lambda: str(typer.prompt(f"Choose ({hint})", default=default, show_default=bool(default))),
            )
            indexes = _parse_numbers(raw, len(choices))
            if indexes is None:
                self._console.print(err(f"Enter numbers between 1 and {len(choices)}."))
            elif not multi and len(indexes) > 1:
                self._console.print(err("Pick one number."))
            elif max_items is not None and len(indexes) > max_items:
                self._console.print(err(f"Pick at most {max_items}."))
            else:
                return [choices[i].value for i in indexes]

    @asynccontextmanager
    async def status(self, msg: str) -> AsyncGenerator[None]:
        with self._console.status(msg):
            yield

    @asynccontextmanager
    async def stream(
        self, prefix: RenderableType | None = None, *, render: StreamRender | None = None
    ) -> AsyncGenerator[StreamSink]:
        """Write each chunk as it comes; a line terminal can't redraw what it wrote, so ``render`` is unused."""
        if prefix is not None:
            self._console.print(prefix, end="")
        sink = _FileSink(self._console.file)
        try:
            yield sink
        finally:
            sink.write("\n")
