"""Every command's body, registered under its Typer path.

A command is written once, as ``async def body(r: Renderer, **params)``, and
reached two ways: ``arcana providers add …`` (its Typer callback builds the
renderer and runs the body) and ``/providers add …`` inside the session (the
slash-command registry parses the line with the same Typer command and awaits
the body with the session's renderer). :func:`command_impl` is what joins the
two: it records the body under the command's Typer path, and the registry
looks it up there.

The contract a body keeps: its parameters after the renderer are the Typer
callback's parameters, by name and type, minus ``--json`` (which picks the
renderer) and the Typer context. So the callback passes each value straight
through, and the session hands the body exactly what the one-shot command
would. A test holds every command to it.

Importing this module is cheap and loads no terminal toolkit: every command
module imports it at CLI start.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

#: A command body: the renderer, then the command's parameters by keyword.
Body = Callable[..., Awaitable[None]]

#: Whether an option's value is a secret, given the value as typed.
SecretTest = Callable[[str], bool]

#: The metavar of a parameter whose value names an agent; the session completes agent names there.
AGENT_METAVAR = "AGENT"

_B = TypeVar("_B", bound=Body)


class CommandGroup(StrEnum):
    """The ``arcana <group> …`` command groups, by their command-line name."""

    AGENT = "agent"
    CARDS = "cards"
    MCP = "mcp"
    MEMORY = "memory"
    PROVIDERS = "providers"
    SOUL = "soul"
    TOOLS = "tools"
    WORLD = "world"


def any_value(_value: str) -> bool:
    """A :data:`SecretTest` for an option whose every value is a secret."""
    return True


def bearer_value(value: str) -> bool:
    """A :data:`SecretTest` for an ``Authorization=Bearer <token>`` header: a secret once it carries a token.

    A blank token isn't one: the command asks for it in a hidden prompt.
    """
    token = value.partition("=")[2].strip()
    if token.lower().startswith("bearer"):
        token = token[len("bearer") :].strip()
    return bool(token)


@dataclass(frozen=True)
class CommandImpl:
    """One command's body and what the session must know about it.

    ``secrets`` maps each parameter whose value can be a secret (by parameter
    name) to the test that says whether a given value is one: such a value
    never goes on a session's command line. ``admits_server`` marks the command
    that approves an MCP server (named by its ``name`` parameter) into the
    session's tools.
    """

    path: str
    body: Body
    secrets: Mapping[str, SecretTest] = field(default_factory=dict[str, SecretTest])
    admits_server: bool = False


#: Every registered body, keyed by its Typer path (``"providers add"``, ``"cards"``).
IMPLS: dict[str, CommandImpl] = {}


def command_impl(
    path: str, *, secrets: Mapping[str, SecretTest] | None = None, admits_server: bool = False
) -> Callable[[_B], _B]:
    """Register the decorated body as the command at Typer ``path`` (e.g. ``"providers add"``).

    The body is returned unchanged. Registering a path twice is a programming
    error and raises ``ValueError``.
    """

    def register(body: _B) -> _B:
        if path in IMPLS:
            raise ValueError(f"command {path!r} already has a body: {IMPLS[path].body.__qualname__}")
        IMPLS[path] = CommandImpl(path, body, dict(secrets or {}), admits_server)
        return body

    return register
