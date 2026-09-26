"""The renderer port and its adapters — import from here, not the submodules.

* :mod:`~arcana_cli.ui.renderer.port` — the :class:`Renderer` protocol and its value types.
* :mod:`~arcana_cli.ui.renderer.tty` — :class:`TtyRenderer`, Rich console + line prompts.
* :mod:`~arcana_cli.ui.renderer.json_renderer` — :class:`JsonRenderer`, the ``--json`` contract.

A command is written once as ``async def cmd(r: Renderer, ...)``; its Typer
callback hands it :func:`renderer_for` and runs it through
:func:`arcana_cli._async.run_async`.
"""

from arcana_cli.ui.renderer.json_renderer import JsonRenderer
from arcana_cli.ui.renderer.port import (
    Choice,
    JsonAble,
    NonInteractiveError,
    Question,
    Renderer,
    StreamSink,
    Validator,
)
from arcana_cli.ui.renderer.tty import TtyRenderer

__all__ = [
    "Choice",
    "JsonAble",
    "JsonRenderer",
    "NonInteractiveError",
    "Question",
    "Renderer",
    "StreamSink",
    "TtyRenderer",
    "Validator",
    "renderer_for",
]


def renderer_for(json: bool) -> Renderer:
    """The renderer for a one-shot command: :class:`JsonRenderer` under ``--json``, else :class:`TtyRenderer`."""
    return JsonRenderer() if json else TtyRenderer()
