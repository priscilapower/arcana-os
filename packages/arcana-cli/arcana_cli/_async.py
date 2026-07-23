"""Sync → async bridge for the CLI.

Typer command callbacks are synchronous, but the core services they wrap
(``MCPRegistry.discover`` and friends) are fully async. Every command that
touches async core runs its one coroutine through here, so there is a single
``asyncio.run`` per invocation and no nested-event-loop hazards — the command
body stays a straight-line call, and core stays async end to end.
"""

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

_T = TypeVar("_T")


def run_async(coro: Coroutine[object, object, _T]) -> _T:
    """Run ``coro`` to completion on a fresh event loop and return its result."""
    return asyncio.run(coro)
