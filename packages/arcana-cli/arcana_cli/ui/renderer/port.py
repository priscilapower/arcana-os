"""The renderer port — the one seam between a command and the surface it runs on.

A command body is a coroutine that takes a :class:`Renderer` and never touches
the terminal directly: output goes through :meth:`Renderer.emit`, questions
through :meth:`Renderer.ask` / :meth:`Renderer.confirm` / :meth:`Renderer.select`,
and progress through :meth:`Renderer.status` / :meth:`Renderer.stream`. Which
adapter it receives decides where that lands (a Rich console with line prompts,
or the ``--json`` stream that never prompts), so one command body serves every
surface.

The port is a :class:`~typing.Protocol` rather than a base class: adapters (and
the test harness) share no state, only this shape.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Generic, Literal, Protocol, TypeAlias, TypeVar, overload

import typer
from pydantic import BaseModel
from rich.console import RenderableType

from arcana_cli._render import EXIT_ERROR

T = TypeVar("T")

#: Data the ``--json`` surface can serialise. :func:`arcana_cli._render.emit_json`
#: is the encoder; a pydantic model is dumped in ``json`` mode first.
JsonAble: TypeAlias = dict[str, Any] | list[Any] | BaseModel

#: Validates a typed answer: return an error message to re-ask, ``None`` to accept.
#: The message is printed to the user, so it must never quote a secret answer.
Validator: TypeAlias = Callable[[str], str | None]


@dataclass(frozen=True)
class Question:
    """A free-text question for :meth:`Renderer.ask`.

    ``secret`` hides the typed input. The answer is returned to the caller and
    nowhere else — no adapter echoes, logs, or keeps it — and ``repr`` hides the
    default of a secret question so a stored value can't leak through a log line.
    ``flag`` names the command-line option that supplies the answer without a
    prompt; a surface that cannot prompt quotes it in its error.
    """

    prompt: str
    default: str | None = None
    secret: bool = False
    validator: Validator | None = None
    flag: str | None = None

    def __repr__(self) -> str:
        default = "<hidden>" if self.secret and self.default is not None else repr(self.default)
        return f"Question(prompt={self.prompt!r}, default={default}, secret={self.secret}, flag={self.flag!r})"


@dataclass(frozen=True)
class Choice(Generic[T]):
    """One option for :meth:`Renderer.select`.

    ``value`` is what :meth:`Renderer.select` returns when the option is picked;
    ``label`` is its one-line name; ``preview`` is an optional renderable a
    two-pane picker shows beside the list. A ``disabled`` choice can't be picked;
    an adapter shows it greyed out or leaves it out.
    """

    value: T
    label: str
    preview: RenderableType | None = None
    disabled: bool = False


class NonInteractiveError(typer.Exit):
    """Raised when a command needs an answer on a surface that cannot prompt.

    A :class:`typer.Exit` carrying :data:`~arcana_cli._render.EXIT_ERROR`, so a
    Typer command that lets it escape exits with that code. The adapter that
    raises it has already written :attr:`message` to stderr, keeping a ``--json``
    stream on stdout clean.
    """

    def __init__(self, prompt: str, *, flag: str | None = None, surface: str = "--json mode") -> None:
        super().__init__(EXIT_ERROR)
        self.prompt = prompt
        self.flag = flag
        self.message = f"{prompt!r} needs an answer, but {surface} never prompts"
        if flag is not None:
            self.message += f"; pass {flag} instead"

    def __str__(self) -> str:
        return self.message


class StreamSink(Protocol):
    """Receives incremental output (e.g. model tokens) inside :meth:`Renderer.stream`."""

    def write(self, chunk: str) -> None: ...


class Renderer(Protocol):
    """Where a command's output and questions go."""

    def emit(self, renderable: RenderableType | JsonAble) -> None:
        """Show one block of output."""
        ...

    async def ask(self, q: Question) -> str:
        """Ask a free-text question; re-asks until ``q.validator`` accepts."""
        ...

    async def confirm(self, text: str, *, default: bool = False, flag: str | None = None) -> bool:
        """Ask a yes/no question."""
        ...

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
        """Pick one choice (``None`` if cancelled), or several with ``multi=True`` (``[]`` if cancelled).

        ``initial`` pre-selects values (and positions the cursor on the first);
        ``max_items`` caps a multi-select.
        """
        ...

    def status(self, msg: str) -> AbstractAsyncContextManager[None]:
        """Show ``msg`` as an in-progress indicator for the duration of the block."""
        ...

    def stream(self, prefix: RenderableType | None = None) -> AbstractAsyncContextManager[StreamSink]:
        """Open a block of incremental output, optionally led by ``prefix``."""
        ...
