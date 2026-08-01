"""Top-level CLI commands: init, status, run."""

import asyncio
import importlib.util
import json
from uuid import UUID

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory import EmbeddingGateway, load_memory_config
from arcana.memory.federation import MemoryFederation
from arcana.models.adapters.fastembed_embedding import FastEmbedEmbeddingAdapter
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.types.agent import Agent as AgentRecord
from arcana.world import NoRouteAskUser, RoutingAuditLog, WorldEngine, WorldStore
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


def find_agent(name_or_id: str, reg: AgentRegistry) -> AgentRecord | None:
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


def build_world_engine(reg: AgentRegistry) -> WorldEngine:
    """A WorldEngine over the on-disk agents, rules, and routing audit log."""
    return WorldEngine(
        reg,
        store=WorldStore(ARCANA_HOME),
        audit=RoutingAuditLog(ARCANA_HOME / "world" / "routing_audit.jsonl"),
    )


def resolve_embedding_gateway() -> EmbeddingGateway | None:
    """Best-effort embedder for the GLOBAL vector tier, or None for SQLite-only.

    Uses in-process FastEmbed when the ``arcana-core[embed]`` extra is installed —
    that install is the user's opt-in to embeddings, and the adapter needs no
    running server. Absent it, the global tier is disabled and agents keep a
    private SQLite memory, so a zero-config install still works.
    """
    if importlib.util.find_spec("fastembed") is None:
        return None
    return EmbeddingGateway([FastEmbedEmbeddingAdapter()])


async def build_session_runtime(
    reg: AgentRegistry,
    record: AgentRecord,
    gateway: ModelGateway,
    sm: SessionManager,
    *,
    no_memory: bool,
) -> tuple[RuntimeAgent, MemoryFederation | None]:
    """Assemble a runtime agent + its memory federation for `run` and `chat`.

    Reads the memory block from ``config.json``, resolves the GLOBAL-tier
    embedder, and delegates to ``build_runtime_with_memory``. Both the one-shot
    ``run`` and the interactive ``chat`` drive the exact same agent+memory path
    through here so they never diverge. Returns the agent together with the
    federation it created (``None`` when memory is off) so the caller closes it.
    """
    memory_cfg = load_memory_config(ARCANA_HOME)
    memory_enabled = memory_cfg.enabled and not no_memory
    embedding = resolve_embedding_gateway() if memory_enabled and memory_cfg.global_ == "vector" else None
    return await reg.build_runtime_with_memory(
        record,
        gateway,
        home=ARCANA_HOME,
        enabled=memory_enabled,
        embedding=embedding,
        session_manager=sm,
        extraction=memory_cfg.extraction,
    )


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

        config: dict[str, object] = {
            "version": "0.1.0",
            "default_model": None,
            "briefing_time": "08:00",
            "memory": {
                "enabled": True,
                "private": "sqlite",
                "global": "vector",
                "pools": [],
                "extraction": {
                    "strategy": "heuristic",
                    "agent_confidence_cap": 0.7,
                    "summarise_on_close": True,
                    "min_confidence_to_store": 0.3,
                },
            },
        }
        (ARCANA_HOME / "config.json").write_text(json.dumps(config, indent=2))
        world: dict[str, object] = {
            "active_spread": None,
            "routing_rules": [],
            "default_agent_id": None,
            "retry_window_s": 60,
        }
        (ARCANA_HOME / "world.json").write_text(json.dumps(world, indent=2))

    console.print(
        make_panel_fit(
            f"[bold {GREEN}]Arcana OS initialised.[/]\n\n"
            f"Home: [{ACCENT}]{ARCANA_HOME}[/]\n\n"
            f"Next step: {cmd('arcana connect model')}",
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
    no_memory: bool = typer.Option(False, "--no-memory", help="Run stateless — do not load or persist memory"),
) -> None:
    """Run a prompt specifying --agent directly."""

    async def _run() -> None:
        if not prompt.strip():
            console.print(err("Prompt cannot be empty."))
            raise typer.Exit(1)

        if session_id and continue_:
            console.print(err("--session and --continue are mutually exclusive."))
            raise typer.Exit(1)

        reg = AgentRegistry(ARCANA_HOME / "agents")

        # An explicit --agent bypasses routing; without one, The World's router
        # resolves the agent. Either way a RoutingDecision is audited before the
        # agent runs.
        explicit: AgentRecord | None = None
        if agent:
            explicit = find_agent(agent, reg)
            if explicit is None:
                console.print(err(f"No agent '{agent}'."))
                raise typer.Exit(1)

        try:
            decision = build_world_engine(reg).route(prompt, explicit_agent=explicit)
        except NoRouteAskUser as exc:
            console.print(err("The World couldn't pick an agent. Name one with --agent <name>."))
            raise typer.Exit(1) from exc

        if explicit is not None:
            record = explicit
        else:
            # route() raises NoRouteAskUser rather than resolving to None, so a
            # returned decision always names an agent here.
            assert decision.resolved_agent_id is not None
            record = reg.get(decision.resolved_agent_id)
            if record is None:
                console.print(err("The routed agent could not be loaded."))
                raise typer.Exit(1)
            console.print(dim(f"The World routed to {record.name} · {decision.layer.value}"))

        model_str = record.model
        if not model_str:
            console.print(
                err(
                    f"No model configured for agent '{record.name}'. "
                    f"Run: arcana agent edit {record.name} --model <provider/model_id>"
                )
            )
            raise typer.Exit(1)

        store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
        accent = card_color(record.card)
        console.print(dim(f"Agent: {record.name} · {record.card.value} · {model_str}"))

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
                runtime_agent, federation = await build_session_runtime(reg, record, gw, sm, no_memory=no_memory)
                try:
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
                finally:
                    # Release the private SQLite handle and any vector store with the run.
                    if federation is not None:
                        await federation.aclose()
        except Exception as exc:
            console.print(err(f"Error: {exc}"))
            raise typer.Exit(1) from exc

        short_id = str(session.id)[:8]
        console.print(dim(f"session: {short_id}  ·  continue with  --session {session.id}  (or --continue)"))

    asyncio.run(_run())
