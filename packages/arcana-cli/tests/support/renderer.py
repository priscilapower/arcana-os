"""``RecordingRenderer`` — the renderer-port fake for command tests.

A command coroutine takes a :class:`~arcana_cli.ui.renderer.Renderer`; tests hand
it one of these instead of a terminal. It records everything the command emits
and notes (and every question, status and stream it opens) and answers questions from
scripts given up front. A question the script didn't anticipate fails the test
loudly instead of blocking.

Typical use::

    r = RecordingRenderer(answers=["my-agent"], confirms=[True], selections=[Card.HERMIT])
    await create_agent(r)
    assert "Created" in r.text()
"""

import io
from collections import deque
from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any, Literal, TypeVar, overload

import typer
from rich.console import Console, RenderableType

from arcana_cli.ui.renderer import Choice, JsonAble, Question, StatusHandle, StreamRender, StreamSink, WaitHandle

T = TypeVar("T")


#: One entry of :attr:`RecordingRenderer.events`: ``("status", msg)``, ``("stop", msg)`` or ``("chunk", text)``.
Event = tuple[Literal["status", "stop", "chunk"], str]


class _ListSink:
    def __init__(self, chunks: list[str], events: list[Event]) -> None:
        self._chunks = chunks
        self._events = events

    def write(self, chunk: str) -> None:
        self._chunks.append(chunk)
        self._events.append(("chunk", chunk))


class _RecordedStatus:
    def __init__(self, msg: str, events: list[Event]) -> None:
        self._msg = msg
        self._events = events
        self._stopped = False

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._events.append(("stop", self._msg))


class RecordingRenderer:
    """Records output and answers questions from scripts.

    ``answers`` feed :meth:`ask` (a blank answer takes the question's default; a
    validator rejection consumes the next answer, as a re-ask would);
    ``confirms`` feed :meth:`confirm`; ``selections`` feed
    :meth:`select` — each entry is the picked *value* (``None`` to cancel), or a
    list of values for a multi-select. A scripted selection must be one of the
    offered, enabled choices.

    :attr:`events` orders statuses, their stops and streamed chunks, so a test
    can check that a status ended before the first chunk arrived.

    A :meth:`waiting` block is recorded in :attr:`waits` (its message) and what
    it shows in :attr:`shown`; ``call_off_waits`` makes every wait raise
    :class:`typer.Abort` as a user pressing Esc would, once it has shown something.
    """

    def __init__(
        self,
        *,
        answers: Iterable[str] = (),
        confirms: Iterable[bool] = (),
        selections: Iterable[object] = (),
        call_off_waits: bool = False,
    ) -> None:
        self.emitted: list[RenderableType | JsonAble] = []
        self.notes: list[RenderableType] = []
        self.questions: list[Question] = []
        self.rejections: list[str] = []
        self.confirmations: list[str] = []
        self.offered: list[Sequence[Choice[Any]]] = []
        self.select_options: list[dict[str, Any]] = []
        self.statuses: list[str] = []
        self.streamed: list[str] = []
        self.events: list[Event] = []
        self.waits: list[str] = []
        self.shown: list[RenderableType] = []
        self._call_off_waits = call_off_waits
        self._answers = deque(answers)
        self._confirms = deque(confirms)
        self._selections = deque(selections)

    # ── output ────────────────────────────────────────────────────────────

    def emit(self, renderable: RenderableType | JsonAble) -> None:
        self.emitted.append(renderable)

    def note(self, renderable: RenderableType) -> None:
        self.notes.append(renderable)

    def text(self, width: int = 100) -> str:
        """Everything emitted, rendered as plain text (no ANSI) at ``width`` columns."""
        return _plain(self.emitted, width)

    def shown_text(self, width: int = 100) -> str:
        """Everything a :meth:`waiting` block showed, rendered as plain text."""
        return _plain(self.shown, width)

    def notes_text(self, width: int = 100) -> str:
        """Every note, rendered as plain text (no ANSI) at ``width`` columns."""
        return _plain(self.notes, width)

    # ── questions ─────────────────────────────────────────────────────────

    async def ask(self, q: Question) -> str:
        self.questions.append(q)
        while True:
            if not self._answers:
                raise AssertionError(f"unscripted ask: {q!r}")
            answer = self._answers.popleft()
            if not answer and q.default is not None:  # a blank answer takes the default, as at a real prompt
                answer = q.default
            problem = q.validator(answer) if q.validator is not None else None
            if problem is None:
                return answer
            self.rejections.append(problem)

    async def confirm(self, text: str, *, default: bool = False, flag: str | None = None) -> bool:
        self.confirmations.append(text)
        if not self._confirms:
            raise AssertionError(f"unscripted confirm: {text!r}")
        return self._confirms.popleft()

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
        self.offered.append(choices)
        self.select_options.append(
            {"multi": multi, "initial": list(initial), "title": title, "max_items": max_items, "flag": flag}
        )
        if not self._selections:
            raise AssertionError(f"unscripted select: {title or [c.label for c in choices]!r}")
        scripted = self._selections.popleft()
        if scripted is None:
            return [] if multi else None
        wanted = list(scripted) if multi and isinstance(scripted, list) else [scripted]
        picked = [_offered(choices, value) for value in wanted]
        if multi:
            if max_items is not None and len(picked) > max_items:
                raise AssertionError(f"scripted {len(picked)} selections, max_items is {max_items}")
            return picked
        return picked[0]

    # ── progress ──────────────────────────────────────────────────────────

    @asynccontextmanager
    async def status(self, msg: str) -> AsyncGenerator[StatusHandle]:
        self.statuses.append(msg)
        self.events.append(("status", msg))
        status = _RecordedStatus(msg, self.events)
        try:
            yield status
        finally:
            status.stop()

    @asynccontextmanager
    async def stream(
        self, prefix: RenderableType | None = None, *, render: StreamRender | None = None
    ) -> AsyncGenerator[StreamSink]:
        """Chunks land in :attr:`streamed`; with ``render``, the finished block is emitted too."""
        if prefix is not None:
            self.emitted.append(prefix)
        start = len(self.streamed)
        try:
            yield _ListSink(self.streamed, self.events)
        finally:
            text = "".join(self.streamed[start:])
            if render is not None and text:
                self.emitted.append(render(text))

    @asynccontextmanager
    async def waiting(self, msg: str, *, title: str = "") -> AsyncGenerator[WaitHandle]:
        self.waits.append(msg)
        yield _RecordedWait(self.shown, call_off=self._call_off_waits)


class _RecordedWait:
    def __init__(self, shown: list[RenderableType], *, call_off: bool) -> None:
        self._shown = shown
        self._call_off = call_off

    def show(self, renderable: RenderableType) -> None:
        self._shown.append(renderable)
        if self._call_off:
            raise typer.Abort()


def _plain(renderables: Iterable[RenderableType | JsonAble], width: int) -> str:
    console = Console(width=width, record=True, file=io.StringIO(), color_system=None)
    for renderable in renderables:
        console.print(renderable)
    return console.export_text()


def _offered(choices: Sequence[Choice[T]], value: object) -> T:
    for choice in choices:
        if choice.value == value:
            if choice.disabled:
                raise AssertionError(f"scripted selection {value!r} is disabled")
            return choice.value
    raise AssertionError(f"scripted selection {value!r} was not offered")
