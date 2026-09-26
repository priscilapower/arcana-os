"""Arcana OS CLI — entry point.

``arcana`` with no command opens the interactive session, exactly as ``arcana
chat`` does (The World picks the agent); ``arcana --help`` lists the commands.
"""

import typer

from arcana_cli.command_impl import CommandGroup
from arcana_cli.commands import agent, cards, chat, mcp, memory, providers, run, soul, tools, world

app = typer.Typer(
    name="arcana",
    help="Arcana OS — The OS that gives your agents a soul. Run with no command to open a chat session.",
    rich_markup_mode="rich",
)

app.add_typer(agent.app, name=CommandGroup.AGENT)
app.add_typer(cards.app, name=CommandGroup.CARDS)
app.add_typer(mcp.app, name=CommandGroup.MCP)
app.add_typer(memory.app, name=CommandGroup.MEMORY)
app.add_typer(providers.app, name=CommandGroup.PROVIDERS)
app.add_typer(soul.app, name=CommandGroup.SOUL)
app.add_typer(tools.app, name=CommandGroup.TOOLS)
app.add_typer(world.app, name=CommandGroup.WORLD)

app.command(name="run")(run.run_cmd)
app.command(name="chat")(chat.chat_cmd)
app.command(name="init")(run.init_cmd)
app.command(name="status")(run.status_cmd)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    no_mouse: bool = typer.Option(False, "--no-mouse", help=f"With no command: {chat.NO_MOUSE_HELP}"),
) -> None:
    if ctx.invoked_subcommand is None:
        chat.open_chat(no_mouse=no_mouse)


if __name__ == "__main__":
    app()
