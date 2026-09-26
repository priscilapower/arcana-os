"""CLI commands for managing soul.md — the user's global context file.

``show`` is a renderer-agnostic coroutine (``show_soul``) whose result is a
:class:`~arcana_cli.ui.renderer.Presentable`: the file as a terminal shows it,
or its path, presence and content under ``--json``. ``edit`` hands the terminal
to ``$EDITOR``, so it has no ``--json`` mode.
"""

import click
import typer

from arcana_cli._async import run_async
from arcana_cli.constants import ARCANA_HOME
from arcana_cli.ui.renderer import Renderer, View, fail, renderer_for
from arcana_cli.ui.theme import dim

app = typer.Typer(help="Manage your soul.md — global user context injected into every agent.")

_SOUL_PATH = ARCANA_HOME / "soul.md"

_TEMPLATE = """\
# Your name

## About me
<!-- Who you are, your role, your domain. This rarely changes. -->

## Current context
<!-- What you're actively working on. -->

## Preferences
<!-- How you like agents to communicate.
     e.g. concise by default / show code not descriptions / flag risks proactively -->

## Working style
<!-- Optional: your rhythms, how you make decisions, async preferences. -->
"""


@app.command("edit")
def edit_cmd() -> None:
    """Open soul.md in $EDITOR, creating it from a template on first use."""
    run_async(prepare_soul(renderer_for(json=False)))
    click.edit(filename=str(_SOUL_PATH))


async def prepare_soul(r: Renderer) -> None:
    """Make sure soul.md exists (from the template on first use); fails when Arcana isn't initialised."""
    if not ARCANA_HOME.exists():
        fail(r, "Arcana not initialised. Run: arcana init")
    if not _SOUL_PATH.exists():
        _SOUL_PATH.write_text(_TEMPLATE, encoding="utf-8")


@app.command("show")
def show_cmd(json_: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    """Print the current soul.md, or a hint if it doesn't exist."""
    run_async(show_soul(renderer_for(json_)))


async def show_soul(r: Renderer) -> None:
    """The current soul.md, or a hint when there is none yet."""
    path = str(_SOUL_PATH)
    if not _SOUL_PATH.exists():
        r.emit(View(dim("No soul.md yet — run 'arcana soul edit'"), {"path": path, "exists": False, "content": None}))
        return
    content = _SOUL_PATH.read_text(encoding="utf-8")
    r.emit(View(content, {"path": path, "exists": True, "content": content}))
