"""Top-level CLI commands: init, status, run.

The command bodies (``init_home``, ``show_status``, ``run_turn``) are
renderer-agnostic coroutines; the Typer callbacks pick the renderer and run
them. ``arcana run`` keeps stdout for the reply alone — notes and the spinner go
to stderr — so ``arcana run --stream … | cat`` is the tokens and nothing else,
and ``--json`` prints exactly one JSON document.
"""

import importlib.util
import json
import os
from dataclasses import dataclass
from uuid import UUID

import typer
from rich.console import Console

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory import EmbeddingGateway, load_memory_config
from arcana.memory.federation import MemoryFederation
from arcana.models.adapters.fastembed_embedding import FastEmbedEmbeddingAdapter
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.tools import MCPRegistry, ToolConfirmer, default_tool_gateway
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana.types.session import Session
from arcana.world import (
    LearningSignalLog,
    NoRouteAskUser,
    QualitySignalSink,
    ReflexClassifier,
    RoutingAuditLog,
    WorldEngine,
    WorldStore,
)
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_ERROR
from arcana_cli.constants import ARCANA_HOME
from arcana_cli.ui.renderer import Renderer, renderer_for
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


class AmbiguousAgentError(ValueError):
    """More than one live agent has the name asked for; the message lists their IDs."""

    def __init__(self, name: str, ids: list[UUID]) -> None:
        self.headline = f"Ambiguous agent name '{name}'. Use one of these IDs:"
        super().__init__("\n".join([self.headline, *(f"  {i}" for i in ids)]))
        self.ids = ids


def resolve_agent(name_or_id: str, reg: AgentRegistry) -> AgentRecord | None:
    """Look up a live agent by UUID or exact name; ``None`` if there is none.

    Raises :class:`AmbiguousAgentError` when the name matches more than one agent.
    """
    try:
        uid = UUID(name_or_id)
        record = reg.get(uid)
        if record is not None and not record.is_archived:
            return record
        return None
    except ValueError:
        pass
    matches = [a for a in reg.list() if a.name == name_or_id]
    if len(matches) > 1:
        raise AmbiguousAgentError(name_or_id, [a.id for a in matches])
    return matches[0] if matches else None


def find_agent(name_or_id: str, reg: AgentRegistry) -> AgentRecord | None:
    """:func:`resolve_agent`, printing an ambiguous name's IDs and exiting 1 instead of raising."""
    try:
        return resolve_agent(name_or_id, reg)
    except AmbiguousAgentError as exc:
        console.print(err(exc.headline))
        for i in exc.ids:
            console.print(f"  {i}")
        raise typer.Exit(EXIT_ERROR) from exc


def build_world_engine(
    reg: AgentRegistry,
    *,
    reflex: ReflexClassifier | None = None,
    sessions: SessionManager | None = None,
    signals: QualitySignalSink | None = None,
) -> WorldEngine:
    """A WorldEngine over the on-disk agents, rules, and routing audit log.

    Passing a ``reflex`` classifier enables model-backed semantic routing;
    ``sessions`` + ``signals`` enable the ``USER_RETRY`` learning signal. All are
    optional — the default engine routes deterministically over the file-system inputs.
    """
    return WorldEngine(
        reg,
        store=WorldStore(ARCANA_HOME),
        audit=RoutingAuditLog(ARCANA_HOME / "world" / "routing_audit.jsonl"),
        reflex=reflex,
        sessions=sessions,
        signals=signals,
    )


# The reflex model The World routes with, as a ``provider/model_id`` reference.
# Set it to turn on semantic routing; unset, routing stays deterministic.
REFLEX_MODEL_ENV = "ARCANA_REFLEX_MODEL"


def resolve_reflex_classifier(gateway: ModelGateway) -> ReflexClassifier | None:
    """Build the reflex classifier from the reflex-model reference, or ``None``.

    Reads the model reference from ``ARCANA_REFLEX_MODEL``; absent it, The World
    has no model to route with (the no-model tier) and the classifier is skipped.
    """
    reflex_model = os.environ.get(REFLEX_MODEL_ENV, "").strip()
    if not reflex_model:
        return None
    return ReflexClassifier(gateway, reflex_model)


def build_signal_sink() -> QualitySignalSink:
    """The learning-loop sink for quality signals (append-only JSONL)."""
    return LearningSignalLog(ARCANA_HOME / "world" / "quality_signals.jsonl")


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
    confirmer: ToolConfirmer | None = None,
    tool_registry: MCPRegistry | None = None,
) -> tuple[RuntimeAgent, MemoryFederation | None]:
    """Assemble a runtime agent + its memory federation for `run` and `chat`.

    Reads the memory block from ``config.json``, resolves the GLOBAL-tier
    embedder, and delegates to ``build_runtime_with_memory``. Both the one-shot
    ``run`` and the interactive ``chat`` drive the exact same agent+memory path
    through here so they never diverge. Returns the agent together with the
    federation it created (``None`` when memory is off) so the caller closes it.
    ``confirmer`` is the interactive approver a ``REQUIRE_CONFIRMATION`` guardrail
    asks; without one such a rule denies. ``tool_registry`` is the set of MCP
    servers the agent's tools may come from; omitted, every configured server.
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
        tool_gateway=(
            default_tool_gateway(record.id, home=ARCANA_HOME, registry=tool_registry)
            if tool_registry is not None
            else None
        ),
        confirmer=confirmer,
    )


def _write_home() -> None:
    """Lay out a fresh ``~/.arcana`` with its default config and world files."""
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


async def init_home(r: Renderer) -> None:
    """Create ``~/.arcana`` and its defaults; a no-op when it already exists."""
    if ARCANA_HOME.exists():
        r.emit(warn("~/.arcana already exists. Nothing to do."))
        raise typer.Exit()

    async with r.status(f"[bold {GREEN}]Initialising Arcana OS...[/]"):
        _write_home()

    r.emit(
        make_panel_fit(
            f"[bold {GREEN}]Arcana OS initialised.[/]\n\n"
            f"Home: [{ACCENT}]{ARCANA_HOME}[/]\n\n"
            f"Next step: {cmd('arcana connect model')}",
            title="Arcana OS",
        )
    )


async def show_status(r: Renderer) -> None:
    """Show the home directory and how many agents and model connections it holds."""
    if not ARCANA_HOME.exists():
        r.emit(err("Arcana not initialised. Run: arcana init"))
        raise typer.Exit(EXIT_ERROR)

    agent_count = len(AgentRegistry(ARCANA_HOME / "agents").list())
    conn_count = len(ConnectionStore(ARCANA_HOME / "connections" / "models.json").all())

    table = make_table("Arcana OS — Status")
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Home", str(ARCANA_HOME))
    table.add_row("Agents", str(agent_count))
    table.add_row("Connections", str(conn_count))

    r.emit(table)


def init_cmd() -> None:
    """Initialise Arcana OS — creates ~/.arcana/ and sets up The World."""
    run_async(init_home(renderer_for(json=False)))


def status_cmd() -> None:
    """Show full system status — agents, connections, The World."""
    run_async(show_status(renderer_for(json=False)))


class RunError(Exception):
    """A run that stopped before (or while) the agent answered.

    ``message`` is shown to the user as is — an error line on a terminal, the
    ``error`` object under ``--json`` — and ``code`` is the exit status.
    """

    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class TurnResult:
    """What one ``arcana run`` turn produced."""

    agent: str
    card: Card
    session_id: UUID
    response: str
    input_tokens: int = 0
    output_tokens: int = 0

    def to_json(self) -> dict[str, object]:
        """The ``--json`` document; ``usage`` appears only when the model reported token counts."""
        doc: dict[str, object] = {"agent": self.agent, "session_id": str(self.session_id), "response": self.response}
        if self.input_tokens or self.output_tokens:
            doc["usage"] = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
        return doc


async def run_turn(
    r: Renderer,
    prompt: str,
    *,
    agent: str | None = None,
    stream: bool = False,
    session_id: str | None = None,
    continue_: bool = False,
    no_memory: bool = False,
) -> TurnResult:
    """Route ``prompt`` to an agent (or use ``agent``), run one turn, and return what it produced.

    Which agent answered and which session it used go to :meth:`Renderer.note`;
    a ``thinking`` status shows until the reply is ready — with ``stream``,
    until its first chunk, after which the chunks go to :meth:`Renderer.stream`.
    Showing the finished reply is the caller's: a panel, or a JSON document.
    Anything that stops the run raises :class:`RunError`.
    """
    if not prompt.strip():
        raise RunError("Prompt cannot be empty.")
    if session_id and continue_:
        raise RunError("--session and --continue are mutually exclusive.")

    reg = AgentRegistry(ARCANA_HOME / "agents")

    # An explicit --agent bypasses routing; without one, The World's router
    # resolves the agent. Either way a RoutingDecision is audited before the
    # agent runs.
    explicit: AgentRecord | None = None
    if agent:
        try:
            explicit = resolve_agent(agent, reg)
        except AmbiguousAgentError as exc:
            raise RunError(str(exc)) from exc
        if explicit is None:
            raise RunError(f"No agent '{agent}'.")

    store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
    sm = SessionManager(ARCANA_HOME / "agents")

    # One gateway serves both routing (the reflex call, when enabled) and
    # the agent run, so a single connection pool is opened and closed.
    async with ModelGateway(connections=store) as gw:
        engine = build_world_engine(
            reg,
            reflex=resolve_reflex_classifier(gw),
            sessions=sm,
            signals=build_signal_sink(),
        )
        try:
            decision = await engine.route(prompt, explicit_agent=explicit)
        except NoRouteAskUser as exc:
            raise RunError("The World couldn't pick an agent. Name one with --agent <name>.") from exc

        if explicit is not None:
            record = explicit
        else:
            # route() raises NoRouteAskUser rather than resolving to None, so a
            # returned decision always names an agent here.
            assert decision.resolved_agent_id is not None
            record = reg.get(decision.resolved_agent_id)
            if record is None:
                raise RunError("The routed agent could not be loaded.")
            r.note(dim(f"The World routed to {record.name} · {decision.layer.value}"))
            if decision.low_confidence:
                r.note(warn(f"couldn't confidently route — using {record.name}"))

        model_str = record.model
        if not model_str:
            raise RunError(
                f"No model configured for agent '{record.name}'. "
                f"Run: arcana agent edit {record.name} --model <provider/model_id>"
            )

        r.note(dim(f"Agent: {record.name} · {record.card.value} · {model_str}"))

        session = _resolve_session(r, sm, record, session_id, continue_)
        response = await _run_agent_turn(r, reg, record, gw, sm, session, prompt, stream=stream, no_memory=no_memory)

    return TurnResult(
        agent=record.name,
        card=record.card,
        session_id=session.id,
        response=response,
        input_tokens=session.total_input_tokens,
        output_tokens=session.total_output_tokens,
    )


def run_cmd(
    prompt: str = typer.Argument(..., help="The prompt to run"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    stream: bool = typer.Option(False, "--stream", "-s", help="Stream output token by token"),
    session_id: str | None = typer.Option(None, "--session", help="Resume a specific session by UUID"),
    continue_: bool = typer.Option(False, "--continue", help="Resume the agent's most recent session"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Run stateless — do not load or persist memory"),
    json_: bool = typer.Option(False, "--json", help="Emit the reply as one JSON document (not with --stream)"),
) -> None:
    """Run a prompt specifying --agent directly."""
    if json_ and stream:
        raise typer.BadParameter("can't be combined with --stream", param_hint="'--json'")

    r = renderer_for(json_)
    try:
        result = run_async(
            run_turn(
                r, prompt, agent=agent, stream=stream, session_id=session_id, continue_=continue_, no_memory=no_memory
            )
        )
    except RunError as exc:
        r.emit({"error": {"code": exc.code, "message": exc.message}} if json_ else err(exc.message))
        raise typer.Exit(exc.code) from exc

    if json_:
        r.emit(result.to_json())
        return
    if not stream:
        r.emit(make_panel(result.response, card=result.card))
    short_id = str(result.session_id)[:8]
    r.note(dim(f"session: {short_id}  ·  continue with  --session {result.session_id}  (or --continue)"))


def _resolve_session(
    r: Renderer, sm: SessionManager, record: AgentRecord, session_id: str | None, continue_: bool
) -> Session:
    """Load, resume, or start the session a run should use.

    ``--session`` loads a specific session (error if missing/invalid);
    ``--continue`` resumes the agent's most recent one (or starts fresh if none);
    otherwise a new session is started.
    """
    if session_id:
        try:
            sid = UUID(session_id)
        except ValueError as e:
            raise RunError(f"Invalid session id: '{session_id}'") from e
        session = sm.load(record.id, sid)
        if session is None:
            raise RunError(f"Session '{session_id}' not found for agent '{record.name}'.")
        return session
    if continue_:
        prior = sm.list_sessions(record.id)
        if prior:
            session = prior[-1]
            r.note(dim(f"Resuming session {str(session.id)[:8]}…"))
            return session
        r.note(dim("No prior sessions found — starting a new one."))
    return sm.start(record.id)


async def _run_agent_turn(
    r: Renderer,
    reg: AgentRegistry,
    record: AgentRecord,
    gw: ModelGateway,
    sm: SessionManager,
    session: Session,
    prompt: str,
    *,
    stream: bool,
    no_memory: bool,
) -> str:
    """Run (or stream) one turn against the resolved agent, releasing memory after; returns the reply.

    The federation is closed on every way out — a finished turn, a failure, or
    a cancellation (Ctrl+C) partway through a stream.
    """
    thinking = f"[bold {card_color(record.card)}]{PROMPT} thinking...[/]"
    try:
        runtime_agent, federation = await build_session_runtime(reg, record, gw, sm, no_memory=no_memory)
        try:
            if not stream:
                async with r.status(thinking):
                    return await runtime_agent.run(prompt, session=session)
            chunks: list[str] = []
            # The status sits inside the stream so it is gone before the
            # stream's closing newline, even when no chunk ever arrives.
            async with r.stream() as sink, r.status(thinking) as status:
                async for chunk in runtime_agent.stream(prompt, session=session):
                    status.stop()
                    sink.write(chunk)
                    chunks.append(chunk)
            return "".join(chunks)
        finally:
            # Release the private SQLite handle and any vector store with the run.
            if federation is not None:
                await federation.aclose()
    except Exception as exc:
        raise RunError(f"Error: {exc}") from exc
