"""``RecordingRenderer`` — the renderer-port fake for command tests.

A command coroutine takes a :class:`~arcana_cli.ui.renderer.Renderer`; tests hand
it one of these instead of a terminal. It records everything the command emits
(and every question, status and stream it opens) and answers questions from
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

from rich.console import Console, RenderableType

from arcana_cli.ui.renderer import Choice, JsonAble, Question, StreamRender, StreamSink

T = TypeVar("T")


class _ListSink:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def write(self, chunk: str) -> None:
        self._chunks.append(chunk)


class RecordingRenderer:
    """Records output and answers questions from scripts.

    ``answers`` feed :meth:`ask` (a validator rejection consumes the next answer,
    as a re-ask would); ``confirms`` feed :meth:`confirm`; ``selections`` feed
    :meth:`select` — each entry is the picked *value* (``None`` to cancel), or a
    list of values for a multi-select. A scripted selection must be one of the
    offered, enabled choices.
    """

    def __init__(
        self,
        *,
        answers: Iterable[str] = (),
        confirms: Iterable[bool] = (),
        selections: Iterable[object] = (),
    ) -> None:
        self.emitted: list[RenderableType | JsonAble] = []
        self.questions: list[Question] = []
        self.rejections: list[str] = []
        self.confirmations: list[str] = []
        self.offered: list[Sequence[Choice[Any]]] = []
        self.select_options: list[dict[str, Any]] = []
        self.statuses: list[str] = []
        self.streamed: list[str] = []
        self._answers = deque(answers)
        self._confirms = deque(confirms)
        self._selections = deque(selections)

    # ── output ────────────────────────────────────────────────────────────

    def emit(self, renderable: RenderableType | JsonAble) -> None:
        self.emitted.append(renderable)

    def text(self, width: int = 100) -> str:
        """Everything emitted, rendered as plain text (no ANSI) at ``width`` columns."""
        console = Console(width=width, record=True, file=io.StringIO(), color_system=None)
        for renderable in self.emitted:
            console.print(renderable)
        return console.export_text()

    # ── questions ─────────────────────────────────────────────────────────

    async def ask(self, q: Question) -> str:
        self.questions.append(q)
        while True:
            if not self._answers:
                raise AssertionError(f"unscripted ask: {q!r}")
            answer = self._answers.popleft()
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
    async def status(self, msg: str) -> AsyncGenerator[None]:
        self.statuses.append(msg)
        yield

    @asynccontextmanager
    async def stream(
        self, prefix: RenderableType | None = None, *, render: StreamRender | None = None
    ) -> AsyncGenerator[StreamSink]:
        """Chunks land in :attr:`streamed`; with ``render``, the finished block is emitted too."""
        if prefix is not None:
            self.emitted.append(prefix)
        start = len(self.streamed)
        try:
            yield _ListSink(self.streamed)
        finally:
            text = "".join(self.streamed[start:])
            if render is not None and text:
                self.emitted.append(render(text))


def _offered(choices: Sequence[Choice[T]], value: object) -> T:
    for choice in choices:
        if choice.value == value:
            if choice.disabled:
                raise AssertionError(f"scripted selection {value!r} is disabled")
            return choice.value
    raise AssertionError(f"scripted selection {value!r} was not offered")
