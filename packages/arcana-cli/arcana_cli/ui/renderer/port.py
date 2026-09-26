"""The renderer port — the one seam between a command and the surface it runs on.

A command body is a coroutine that takes a :class:`Renderer` and never touches
the terminal directly: output goes through :meth:`Renderer.emit`, side remarks
about the run through :meth:`Renderer.note`, questions through
:meth:`Renderer.ask` / :meth:`Renderer.confirm` / :meth:`Renderer.select`, and
progress through :meth:`Renderer.status` / :meth:`Renderer.stream`. Which
adapter it receives decides where that lands (a Rich console with line prompts,
or the ``--json`` stream that never prompts), so one command body serves every
surface.

The port is a :class:`~typing.Protocol` rather than a base class: adapters (and
the test harness) share no state, only this shape.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Generic, Literal, NoReturn, Protocol, TypeAlias, TypeVar, overload

import typer
from pydantic import BaseModel
from rich.console import Console, RenderableType
from rich.markup import escape

from arcana_cli._render import EXIT_ERROR
from arcana_cli.ui.theme import dim, err

T = TypeVar("T")

#: Data the ``--json`` surface can serialise. :func:`arcana_cli._render.emit_json`
#: is the encoder; a pydantic model is dumped in ``json`` mode first.
JsonAble: TypeAlias = dict[str, Any] | list[Any] | BaseModel

#: Turns everything streamed so far into the block shown for it; see :meth:`Renderer.stream`.
StreamRender: TypeAlias = Callable[[str], RenderableType]

#: Validates a typed answer: return an error message to re-ask, ``None`` to accept.
#: The message is printed to the user, so it must never quote a secret answer.
Validator: TypeAlias = Callable[[str], str | None]


def required(answer: str) -> str | None:
    """A :data:`Validator` that refuses a blank answer."""
    return None if answer.strip() else "An answer is required."


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


def initial_indexes(choices: Sequence[Choice[T]], initial: Sequence[T]) -> list[int]:
    """The indexes of the enabled ``choices`` whose values ``initial`` names, in ``initial``'s order.

    An adapter pre-selects these and starts its cursor on the first.
    """
    return [i for value in initial for i, c in enumerate(choices) if c.value == value and not c.disabled]


class NonInteractiveError(typer.Exit):
    """Raised when a command needs an answer on a surface that cannot prompt.

    A :class:`typer.Exit` carrying :data:`~arcana_cli._render.EXIT_ERROR`, so a
    Typer command that lets it escape exits with that code. The adapter that
    raises it has already written :attr:`message` to stderr, keeping a ``--json``
    stream on stdout clean.
    """

    def __init__(
        self, prompt: str, *, flag: str | None = None, surface: str = "--json mode", reason: str | None = None
    ) -> None:
        super().__init__(EXIT_ERROR)
        self.prompt = prompt
        self.flag = flag
        self.message = f"{prompt!r} needs an answer, but {reason or f'{surface} never prompts'}"
        if flag is not None:
            self.message += f"; pass {flag} instead"

    def __str__(self) -> str:
        return self.message


def refuse(
    stderr: Console, prompt: str, *, flag: str | None, surface: str = "", reason: str | None = None
) -> NoReturn:
    """Fail closed on a question that can't be answered: say so on ``stderr``, then raise :class:`NonInteractiveError`.

    ``surface`` names what can't ask it ("…, but <surface> never prompts");
    ``reason`` replaces that clause outright. The message is escaped, so a
    prompt that looks like Rich markup is printed as written.
    """
    error = NonInteractiveError(prompt, flag=flag, surface=surface, reason=reason)
    stderr.print(err(escape(error.message)))
    raise error


class StreamSink(Protocol):
    """Receives incremental output (e.g. model tokens) inside :meth:`Renderer.stream`."""

    def write(self, chunk: str) -> None: ...


class StatusHandle(Protocol):
    """The value of a :meth:`Renderer.status` block."""

    def stop(self) -> None:
        """End the indicator before the block does; a second call (or the block's exit) is a no-op."""
        ...


class Renderer(Protocol):
    """Where a command's output and questions go."""

    def emit(self, renderable: RenderableType | JsonAble) -> None:
        """Show one block of output."""
        ...

    def note(self, renderable: RenderableType) -> None:
        """Show a remark about the command rather than its output (which agent answered, how to resume).

        A console surface writes it to stderr, so a pipe reading stdout gets the
        output alone; the ``--json`` surface does the same, keeping it out of
        the JSON stream.
        """
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

    def status(self, msg: str) -> AbstractAsyncContextManager[StatusHandle]:
        """Show ``msg`` as an in-progress indicator for the duration of the block.

        The block's value can :meth:`~StatusHandle.stop` the indicator early,
        e.g. when the first chunk of a :meth:`stream` arrives, so the two never
        draw over each other.
        """
        ...

    def stream(
        self, prefix: RenderableType | None = None, *, render: StreamRender | None = None
    ) -> AbstractAsyncContextManager[StreamSink]:
        """Open a block of incremental output, optionally led by ``prefix``.

        ``render`` formats the text: a surface that redraws calls it with
        everything streamed so far (the empty string before the first chunk, so
        it can show a placeholder) and shows the result, and the finished block
        is ``render`` of the whole text. A block that received nothing finishes
        as its prefix alone. Without ``render`` the text is shown as it came.
        """
        ...


#: The option that answers a destructive command's confirmation without a prompt.
YES_FLAG = "--yes"

#: What a declined destructive confirmation says before the command exits.
CANCELLED = "Cancelled."


async def confirm_or_cancel(r: Renderer, text: str, *, flag: str = YES_FLAG) -> None:
    """Ask a destructive yes/no question; anything but yes ends the command.

    The default answer is no, and a cancelled dialog counts as no. Declining
    notes :data:`CANCELLED` and exits :data:`~arcana_cli._render.EXIT_ERROR`,
    the code a declined confirmation has always exited with, so a script that
    checks it keeps working. ``flag`` names the option that skips the question.
    """
    if not await r.confirm(text, default=False, flag=flag):
        r.note(dim(CANCELLED))
        raise typer.Exit(EXIT_ERROR)
