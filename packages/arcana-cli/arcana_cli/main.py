"""Arcana OS CLI — entry point."""

import typer
from rich.console import Console

from arcana_cli.commands import agent, cards, providers, run, soul

app = typer.Typer(
    name="arcana",
    help="Arcana OS — The OS that gives your agents a soul.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

app.add_typer(agent.app, name="agent")
app.add_typer(cards.app, name="cards")
app.add_typer(providers.app, name="providers")
app.add_typer(soul.app, name="soul")

app.command(name="run")(run.run_cmd)
app.command(name="init")(run.init_cmd)
app.command(name="status")(run.status_cmd)

if __name__ == "__main__":
    app()
