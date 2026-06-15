"""Top-level CLI commands: init, status, run."""

import asyncio
import json
from uuid import UUID

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.types.agent import Agent as AgentRecord
from arcana_cli.constants import ARCANA_HOME
from arcana_cli.ui.theme import (
    ACCENT,
    GREEN,
    PROMPT,
    card_color,
    cmd,
    dim,
    err,
    make_panel,
    make_panel_fit,
    make_table,
    warn,
)

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
        console.print(err(f"Ambiguous agent name '{name_or_id}'. Use one of these IDs:"))
        for a in matches:
            console.print(f"  {a.id}")
        raise typer.Exit(1)
    return matches[0]


def init_cmd() -> None:
    """Initialise Arcana OS — creates ~/.arcana/ and sets up The World."""
    if ARCANA_HOME.exists():
        console.print(warn("~/.arcana already exists. Nothing to do."))
        raise typer.Exit()

    with console.status(f"[bold {GREEN}]Initialising Arcana OS...[/]"):
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
        make_panel_fit(
            f"[bold {GREEN}]Arcana OS initialised.[/]\n\n"
            f"Home: [{ACCENT}]{ARCANA_HOME}[/]\n\n"
            f"Next step: {cmd('arcana agent create')}",
            title="Arcana OS",
        )
    )


def status_cmd() -> None:
    """Show full system status — agents, connections, The World."""
    if not ARCANA_HOME.exists():
        console.print(err("Arcana not initialised. Run: arcana init"))
        raise typer.Exit(1)

    agent_count = len(AgentRegistry(ARCANA_HOME / "agents").list())
    conn_count = len(ConnectionStore(ARCANA_HOME / "connections" / "models.json").all())

    table = make_table("Arcana OS — Status")
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Home", str(ARCANA_HOME))
    table.add_row("Agents", str(agent_count))
    table.add_row("Connections", str(conn_count))

    console.print(table)


def run_cmd(
    prompt: str = typer.Argument(..., help="The prompt to run"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    stream: bool = typer.Option(False, "--stream", "-s", help="Stream output token by token"),
    session_id: str | None = typer.Option(None, "--session", help="Resume a specific session by UUID"),
    continue_: bool = typer.Option(False, "--continue", help="Resume the agent's most recent session"),
) -> None:
    """Run a prompt specifying --agent directly."""

    async def _run() -> None:
        if not prompt.strip():
            console.print(err("Prompt cannot be empty."))
            raise typer.Exit(1)

        if not agent:
            console.print(err("--agent is required. Use: arcana run <prompt> --agent <name>"))
            raise typer.Exit(1)

        if session_id and continue_:
            console.print(err("--session and --continue are mutually exclusive."))
            raise typer.Exit(1)

        reg = AgentRegistry(ARCANA_HOME / "agents")
        record = _find_agent(agent, reg)
        if record is None:
            console.print(err(f"No agent '{agent}'."))
            raise typer.Exit(1)

        store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
        conn_map = {c.id: c for c in store.all()}
        connection = conn_map.get(record.model_connection_id)
        if connection is None:
            console.print(err(f"Model connection for agent '{record.name}' not found. Run: arcana connect model"))
            raise typer.Exit(1)

        # provider:name/model_id — gateway resolves by connection name so the API key is found
        model_str = f"{connection.provider}:{connection.name}/{connection.model_id}"
        accent = card_color(record.card)
        console.print(dim(f"Agent: {record.name} · {record.card.value} · {connection.name}"))

        sm = SessionManager(ARCANA_HOME / "agents")

        if session_id:
            try:
                sid = UUID(session_id)
            except ValueError as e:
                console.print(err(f"Invalid session id: '{session_id}'"))
                raise typer.Exit(1) from e
            session = sm.load(record.id, sid)
            if session is None:
                console.print(err(f"Session '{session_id}' not found for agent '{record.name}'."))
                raise typer.Exit(1)
        elif continue_:
            prior = sm.list_sessions(record.id)
            if prior:
                session = prior[-1]
                console.print(dim(f"Resuming session {str(session.id)[:8]}…"))
            else:
                session = sm.start(record.id)
                console.print(dim("No prior sessions found — starting a new one."))
        else:
            session = sm.start(record.id)

        try:
            async with ModelGateway(connections=store) as gw:
                runtime_agent = reg.build_runtime(record, gw, model_str, session_manager=sm)
                if stream:
                    live = Live(
                        Spinner("dots", text=f"[bold {accent}]{PROMPT} thinking...[/]"),
                        console=console,
                        transient=True,
                    )
                    live.start()
                    first = True
                    async for chunk in runtime_agent.stream(prompt, session=session):
                        if first:
                            live.stop()
                            first = False
                        print(chunk, end="", flush=True)
                    if first:
                        live.stop()
                    print()
                else:
                    with console.status(
                        f"[bold {accent}]{PROMPT} thinking...[/]",
                        spinner="dots",
                        spinner_style=f"bold {accent}",
                    ):
                        response = await runtime_agent.run(prompt, session=session)
                    console.print(make_panel(response, card=record.card))
        except Exception as exc:
            console.print(err(f"Error: {exc}"))
            raise typer.Exit(1) from exc

        short_id = str(session.id)[:8]
        console.print(dim(f"session: {short_id}  ·  continue with  --session {session.id}  (or --continue)"))

    asyncio.run(_run())
