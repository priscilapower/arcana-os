"""The renderer port and its adapters — import from here, not the submodules.

* :mod:`~arcana_cli.ui.renderer.port` — the :class:`Renderer` protocol, its value types and :func:`fail`.
* :mod:`~arcana_cli.ui.renderer.presentable` — :class:`Presentable` (one result, a human and a
  JSON view), :class:`View` and :class:`Failure`.
* :mod:`~arcana_cli.ui.renderer.tty` — :class:`TtyRenderer`, Rich console + line prompts.
* :mod:`~arcana_cli.ui.renderer.json_renderer` — :class:`JsonRenderer`, the ``--json`` contract.
* :mod:`~arcana_cli.ui.renderer.textual_renderer` — ``TextualRenderer``, the interactive app.
  The one adapter not re-exported here: it loads Textual, which one-shot and
  ``--json`` commands never need, so import it from its module.

A command is written once as ``async def cmd(r: Renderer, ...)``: it emits
:class:`Presentable` results and stops with :func:`fail`, never asking which
surface it is on. Its Typer callback hands it :func:`renderer_for` (``--json``
or not) and runs it through :func:`arcana_cli._async.run_async`.
"""

from arcana_cli.ui.renderer.json_renderer import JsonRenderer
from arcana_cli.ui.renderer.port import (
    CANCELLED,
    YES_FLAG,
    Choice,
    Emittable,
    NonInteractiveError,
    Question,
    Renderer,
    StatusHandle,
    StreamRender,
    StreamSink,
    Validator,
    WaitHandle,
    confirm_or_cancel,
    fail,
    initial_indexes,
    required,
)
from arcana_cli.ui.renderer.presentable import Failure, JsonAble, Presentable, Verbatim, View, lines
from arcana_cli.ui.renderer.tty import TtyRenderer

__all__ = [
    "CANCELLED",
    "YES_FLAG",
    "Choice",
    "Emittable",
    "Failure",
    "JsonAble",
    "JsonRenderer",
    "NonInteractiveError",
    "Presentable",
    "Question",
    "Renderer",
    "StatusHandle",
    "StreamRender",
    "StreamSink",
    "TtyRenderer",
    "Validator",
    "Verbatim",
    "View",
    "WaitHandle",
    "confirm_or_cancel",
    "fail",
    "initial_indexes",
    "lines",
    "renderer_for",
    "required",
]


def renderer_for(json: bool) -> Renderer:
    """The renderer for a one-shot command: :class:`JsonRenderer` under ``--json``, else :class:`TtyRenderer`."""
    return JsonRenderer() if json else TtyRenderer()
