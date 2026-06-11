"""Top-level CLI commands: init, status, run."""

import asyncio
import json
from uuid import UUID

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from arcana.agents.registry import AgentRegistry
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.types.agent import Agent as AgentRecord
from arcana_cli.constants import ARCANA_HOME

console = Console()


def _find_agent(name_or_id: str, reg: AgentRegistry) -> AgentRecord | None:
    """Look up an agent by UUID or exact name. Returns None if not found."""
    try:
        uid = UUID(name_or_id)
        record = reg.get(uid)
        if record is not None and not record.is_archived:
            return record
        return None
    except ValueError:
        pass
    matches = [a for a in reg.list() if a.name == name_or_id]
    if not matches:
        return None
    if len(matches) > 1:
        console.print(f"[red]Ambiguous agent name '{name_or_id}'. Use one of these IDs:[/red]")
        for a in matches:
            console.print(f"  {a.id}")
        raise typer.Exit(1)
    return matches[0]


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

    agent_count = len(AgentRegistry(ARCANA_HOME / "agents").list())
    conn_count = len(ConnectionStore(ARCANA_HOME / "connections" / "models.json").all())

    table = Table(title="🌌 Arcana OS Status", show_header=True)
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Home", str(ARCANA_HOME))
    table.add_row("Agents", str(agent_count))
    table.add_row("Connections", str(conn_count))
    table.add_row("The World", "[green]ready[/green]")
    console.print(table)


def run_cmd(
    prompt: str = typer.Option(..., "--prompt", "-p", help="The prompt to run"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    stream: bool = typer.Option(False, "--stream", "-s", help="Stream output token by token"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Stateless run"),
) -> None:
    """Run a prompt — The World routes it, or specify --agent directly."""

    async def _run() -> None:
        if not prompt.strip():
            console.print("[red]--prompt cannot be empty.[/red]")
            raise typer.Exit(1)

        if not agent:
            console.print("[red]--agent is required. Use: arcana run <prompt> --agent <name>[/red]")
            raise typer.Exit(1)

        reg = AgentRegistry(ARCANA_HOME / "agents")
        record = _find_agent(agent, reg)
        if record is None:
            console.print(f"[red]No agent '{agent}'.[/red]")
            raise typer.Exit(1)

        store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
        conn_map = {c.id: c for c in store.all()}
        connection = conn_map.get(record.model_connection_id)
        if connection is None:
            console.print(
                f"[red]Model connection for agent '{record.name}' not found. Run: arcana connect model[/red]"
            )
            raise typer.Exit(1)

        # provider:name/model_id — gateway resolves by connection name so the API key is found
        model_str = f"{connection.provider}:{connection.name}/{connection.model_id}"
        console.print(f"[dim]Agent: {record.name} · {record.card.value} · {connection.name}[/dim]")

        try:
            async with ModelGateway(connections=store) as gw:
                runtime_agent = reg.build_runtime(record, gw, model_str)
                if stream:
                    async for chunk in runtime_agent.stream(prompt):
                        print(chunk, end="", flush=True)
                    print()
                else:
                    response = await runtime_agent.run(prompt)
                    console.print(response)
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(1) from exc

    asyncio.run(_run())
