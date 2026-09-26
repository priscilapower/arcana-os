"""Output that renders anywhere: one value, a human view and a JSON view.

A command that serves both a terminal and ``--json`` builds its result once, as
a :class:`Presentable`, and emits it; the adapter it was handed picks the view
(:meth:`~Presentable.to_rich` on a terminal or in the session,
:meth:`~Presentable.to_json` under ``--json``). The command never asks which
surface it is on, so the two views can't drift into two code paths.

A failure is a :class:`Failure`, shown through :meth:`Renderer.error
<arcana_cli.ui.renderer.port.Renderer.error>`: an error line and its hints on a
terminal (stderr, off the output), and the ``{"error": {"code", "message"}}``
document on the ``--json`` stream.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel
from rich.console import Group, RenderableType
from rich.markup import escape
from rich.text import Text

from arcana_cli._render import EXIT_ERROR
from arcana_cli.ui.theme import err

#: Data the ``--json`` surface can serialise. :func:`arcana_cli._render.emit_json`
#: is the encoder; a pydantic model is dumped in ``json`` mode first.
JsonAble: TypeAlias = dict[str, Any] | list[Any] | BaseModel


@runtime_checkable
class Presentable(Protocol):
    """A command result with a human view and a JSON view of the same data."""

    def to_rich(self) -> RenderableType:
        """The view a terminal (or the session transcript) shows."""
        ...

    def to_json(self) -> JsonAble:
        """The document the ``--json`` stream carries: lists of objects, ids as strings, no markup."""
        ...


@dataclass(frozen=True)
class View:
    """A :class:`Presentable` built from its two views, for a result that needs no class of its own.

    Build both from the same data in one function, so a field can't reach one
    view and miss the other.
    """

    rich: RenderableType
    json: JsonAble

    def to_rich(self) -> RenderableType:
        return self.rich

    def to_json(self) -> JsonAble:
        return self.json


@dataclass(frozen=True)
class Verbatim:
    """A document shown exactly as written — a Markdown export meant for ``> file`` or ``| less``.

    A line terminal writes the text as is, plus a newline, as ``print`` would:
    no markup, wrapping, cropping or tab expansion. A surface that can only
    render (the session transcript) shows it as plain :class:`~rich.text.Text`.
    """

    text: str

    def __rich__(self) -> Text:
        return Text(self.text)


def lines(*renderables: RenderableType) -> Group:
    """Several blocks as one human view, each printed as its own ``console.print`` would print it."""
    return Group(*renderables)


#: What a :class:`Failure` headline is styled with: markup in, markup out.
Headline: TypeAlias = Callable[[str], str]


@dataclass(frozen=True)
class Failure:
    """Why a command stopped, and the exit code it stops with.

    ``message`` is plain text (never markup): the JSON ``message``, and the
    headline a terminal shows styled by ``headline`` (an error line by default).
    ``details`` are the lines under it on a terminal (hints, candidate IDs), as
    markup; the JSON document carries their plain text as ``details``.
    """

    message: str
    details: tuple[str, ...] = ()
    code: int = EXIT_ERROR
    headline: Headline = err

    def to_rich(self) -> RenderableType:
        return lines(self.headline(escape(self.message)), *self.details)

    def plain_details(self) -> list[str]:
        """``details`` as plain text, without markup or indentation."""
        return [Text.from_markup(d).plain.strip() for d in self.details]
