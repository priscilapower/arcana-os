"""Top-level CLI commands: init, status, run."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
ARCANA_HOME = Path.home() / ".arcana"


def init_cmd() -> None:
    """Initialise Arcana OS — creates ~/.arcana/ and sets up The World."""
    if ARCANA_HOME.exists():
        console.print("[yellow]~/.arcana already exists. Nothing to do.[/yellow]")
        raise typer.Exit()

    with console.status("[bold green]Initialising Arcana OS..."):
        ARCANA_HOME.mkdir(parents=True)
        (ARCANA_HOME / "agents").mkdir()
        (ARCANA_HOME / "connections").mkdir()
        (ARCANA_HOME / "cards" / "core").mkdir(parents=True)
        (ARCANA_HOME / "cards" / "custom").mkdir(parents=True)
        (ARCANA_HOME / "spreads").mkdir()
        (ARCANA_HOME / "vector").mkdir()

        import json

        config = {
            "version": "0.1.0",
            "default_model": None,
            "briefing_time": "08:00",
        }
        (ARCANA_HOME / "config.json").write_text(json.dumps(config, indent=2))
        (ARCANA_HOME / "world.json").write_text(json.dumps({"active_spread": None, "routing_rules": []}, indent=2))

    console.print(
        Panel.fit(
            "[bold green]✨ Arcana OS initialised![/bold green]\n\n"
            f"Home: [cyan]{ARCANA_HOME}[/cyan]\n\n"
            "Next step: [bold]arcana agent create[/bold]",
            title="🌌 Arcana OS",
        )
    )


def status_cmd() -> None:
    """Show full system status — agents, connections, The World."""
    if not ARCANA_HOME.exists():
        console.print("[red]Arcana not initialised. Run: arcana init[/red]")
        raise typer.Exit(1)

    agents_dir = ARCANA_HOME / "agents"
    agent_count = len(list(agents_dir.iterdir())) if agents_dir.exists() else 0

    table = Table(title="🌌 Arcana OS Status", show_header=True)
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Home", str(ARCANA_HOME))
    table.add_row("Agents", str(agent_count))
    table.add_row("The World", "[green]ready[/green]")
    console.print(table)


def run_cmd(
    prompt: str = typer.Argument(..., help="The prompt to run"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name"),
    stream: bool = typer.Option(False, "--stream", "-s", help="Stream output"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Stateless run"),
) -> None:
    """Run a prompt — The World routes it, or specify --agent directly."""
    import asyncio

    async def _run() -> None:
        if agent:
            console.print(f"[dim]Running with agent: {agent}[/dim]")
            # TODO: load agent from registry and run
            console.print(
                "[yellow]Agent registry is implemented in Epic 5. Use the Python API directly for now.[/yellow]"
            )
        else:
            console.print("[dim]Routing via The World...[/dim]")
            console.print("[yellow]World routing is implemented in Epic 7.[/yellow]")

    asyncio.run(_run())
