"""CLI commands for managing soul.md — the user's global context file."""

import click
import typer
from rich.console import Console

from arcana_cli.constants import ARCANA_HOME
from arcana_cli.ui.theme import dim, err

app = typer.Typer(help="Manage your soul.md — global user context injected into every agent.")
console = Console()

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
    if not ARCANA_HOME.exists():
        console.print(err("Arcana not initialised. Run: arcana init"))
        raise typer.Exit(1)

    if not _SOUL_PATH.exists():
        _SOUL_PATH.write_text(_TEMPLATE, encoding="utf-8")

    click.edit(filename=str(_SOUL_PATH))


@app.command("show")
def show_cmd() -> None:
    """Print the current soul.md, or a hint if it doesn't exist."""
    if not _SOUL_PATH.exists():
        console.print(dim("No soul.md yet — run 'arcana soul edit'"))
        return

    content = _SOUL_PATH.read_text(encoding="utf-8")
    console.print(content)
