"""Arcana OS CLI — entry point.

``arcana`` with no command opens the interactive session, exactly as ``arcana
chat`` does (The World picks the agent); ``arcana --help`` lists the commands.
"""

import typer

from arcana_cli.commands import agent, cards, chat, mcp, memory, providers, run, soul, tools, world

app = typer.Typer(
    name="arcana",
    help="Arcana OS — The OS that gives your agents a soul. Run with no command to open a chat session.",
    rich_markup_mode="rich",
)

app.add_typer(agent.app, name="agent")
app.add_typer(cards.app, name="cards")
app.add_typer(mcp.app, name="mcp")
app.add_typer(memory.app, name="memory")
app.add_typer(providers.app, name="providers")
app.add_typer(soul.app, name="soul")
app.add_typer(tools.app, name="tools")
app.add_typer(world.app, name="world")

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
