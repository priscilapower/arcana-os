"""``JsonRenderer`` — the renderer port over the ``--json`` scripting contract.

Every emitted block is one :func:`arcana_cli._render.emit_json` document (a
:class:`~arcana_cli.ui.renderer.presentable.Presentable` emits its JSON view), so
the JSON shapes and exit codes a script relies on are exactly the ones the
commands produced before they took a renderer. A failure is the
:func:`arcana_cli._render.emit_error` document on stdout, in place of a result.
The surface never prompts: a question fails like any other error, raising
:class:`~arcana_cli.ui.renderer.port.NonInteractiveError` (exit ``EXIT_ERROR``)
instead of blocking a pipeline on stdin. Notes go to stderr, so stdout carries
nothing but the documents.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Literal, NoReturn, TypeVar, overload

from pydantic import BaseModel
from rich.console import Console, RenderableType

from arcana_cli._render import EXIT_ERROR, emit_error, emit_json
from arcana_cli.ui.renderer.port import (
    Choice,
    Emittable,
    NonInteractiveError,
    PrintedWait,
    Question,
    StatusHandle,
    StreamRender,
    WaitHandle,
)
from arcana_cli.ui.renderer.presentable import Failure, Presentable

T = TypeVar("T")

#: How :class:`~arcana_cli.ui.renderer.port.NonInteractiveError` names this surface.
JSON_SURFACE = "--json mode"


class _NoStatus:
    def stop(self) -> None:
        pass


class JsonRenderer:
    """Emits JSON documents (a result or an error); refuses Rich renderables and every question."""

    def __init__(self, stderr: Console | None = None) -> None:
        self._stderr = stderr if stderr is not None else Console(stderr=True)

    def _refuse(self, prompt: str, flag: str | None) -> NoReturn:
        """Fail closed on a question: the refusal is the stream's error document."""
        error = NonInteractiveError(prompt, flag=flag, surface=JSON_SURFACE)
        emit_error(EXIT_ERROR, error.message)
        raise error

    def emit(self, renderable: Emittable) -> None:
        """Print one JSON document; a Rich renderable is a programming error here.

        Rejecting it (rather than printing its ANSI form) keeps a stray human
        renderable from corrupting the stream a script is parsing.
        """
        if isinstance(renderable, Presentable):
            renderable = renderable.to_json()
        if isinstance(renderable, BaseModel):
            emit_json(renderable.model_dump(mode="json"))
        elif isinstance(renderable, dict | list):
            emit_json(renderable)
        else:
            raise TypeError(
                f"JsonRenderer.emit takes a dict, list, or pydantic model, not {type(renderable).__name__}; "
                "build the JSON payload for --json output instead of a Rich renderable"
            )

    def note(self, renderable: RenderableType) -> None:
        self._stderr.print(renderable)

    def error(self, failure: Failure) -> None:
        emit_error(failure.code, failure.message, failure.plain_details())

    async def ask(self, q: Question) -> NoReturn:
        self._refuse(q.prompt, q.flag)

    async def confirm(self, text: str, *, default: bool = False, flag: str | None = None) -> NoReturn:
        self._refuse(text, flag)

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
        self._refuse(title or "a selection", flag)

    @asynccontextmanager
    async def status(self, msg: str) -> AsyncGenerator[StatusHandle]:
        """No indicator: stdout carries only JSON documents."""
        yield _NoStatus()

    def stream(self, prefix: RenderableType | None = None, *, render: StreamRender | None = None) -> NoReturn:
        """Incremental output has no JSON form; a command emits the finished result instead.

        A command offering both a stream and ``--json`` rejects the pair as a
        usage error before it gets here.
        """
        raise TypeError("JsonRenderer has no stream; collect the output and emit it as one JSON document")

    @asynccontextmanager
    async def waiting(self, msg: str, *, title: str = "") -> AsyncGenerator[WaitHandle]:
        """The instructions go to stderr, like a note, so stdout carries only JSON documents."""
        yield PrintedWait(self._stderr, msg)
