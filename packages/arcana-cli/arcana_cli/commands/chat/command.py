"""The ``arcana chat`` command, and bare ``arcana``, which opens the same session.

:func:`open_chat` settles everything that can fail before a terminal app
starts: which agent opens the session (the one named, or The World's pick when
none is), that it has a model, and the session to resume. Each failure goes
through :func:`~arcana_cli.ui.renderer.fail` on a line-terminal renderer and
exits with :data:`~arcana_cli._render.EXIT_ERROR`. Only then is the session app
loaded and run (:func:`~arcana_cli.commands.chat.app.run_chat`).
"""

from uuid import UUID

import typer

from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.types.agent import Agent as AgentRecord
from arcana.types.session import Session, SessionTrigger
from arcana.world import NoRouteAskUser
from arcana_cli._async import run_async
from arcana_cli.commands.run import build_world_engine, find_agent
from arcana_cli.constants import ARCANA_HOME
from arcana_cli.ui.renderer import Renderer, TtyRenderer, fail
from arcana_cli.ui.theme import dim

NO_MOUSE_HELP = "Turn off mouse capture for native click-drag selection (no wheel scrolling of the transcript)"


async def _opening_agent(r: Renderer, reg: AgentRegistry, agent: str | None) -> tuple[AgentRecord, tuple[str, ...]]:
    """The agent the session opens with, and the notes (markup) to open the transcript with.

    With ``agent``, that agent. Without one, The World resolves a default agent
    (the opening task carries no text, so only default resolution applies); if
    it can't decide, the user is asked to name one.
    """
    if agent:
        record = find_agent(r, agent, reg)
        if record is None:
            fail(r, f"No agent '{agent}'.")
        return record, ()
    # The user opened the session, so the routing (and the session it opens) is
    # user-triggered even though The World picks the agent.
    try:
        decision = await build_world_engine(reg).route("", trigger_origin=SessionTrigger.USER)
    except NoRouteAskUser:
        fail(r, "The World couldn't pick an agent. Start the chat with --agent <name>.")
    record = reg.get(decision.resolved_agent_id) if decision.resolved_agent_id else None
    if record is None:
        fail(r, "The routed agent could not be loaded.")
    return record, (dim(f"The World opened this chat with {record.name}."),)


def _resumed_session(r: Renderer, sm: SessionManager, record: AgentRecord, session_id: str | None) -> Session | None:
    """The session ``session_id`` names for ``record``, or ``None`` to start a new one."""
    if not session_id:
        return None
    try:
        sid = UUID(session_id)
    except ValueError:
        fail(r, f"Invalid session id: '{session_id}'")
    session = sm.load(record.id, sid)
    if session is None:
        fail(r, f"Session '{session_id}' not found for agent '{record.name}'.")
    return session


async def _open_chat(
    r: Renderer, *, agent: str | None, session_id: str | None, no_memory: bool, no_mouse: bool
) -> None:
    reg = AgentRegistry(ARCANA_HOME / "agents")
    record, notes = await _opening_agent(r, reg, agent)
    if not record.model:
        fail(
            r,
            f"No model configured for agent '{record.name}'. "
            f"Run: arcana agent edit {record.name} --model <provider/model_id>",
        )
    sm = SessionManager(ARCANA_HOME / "agents")
    session = _resumed_session(r, sm, record, session_id)
    # Deferred: the session app loads Textual (over 100 ms), which the one-shot
    # and --json commands sharing this entry point must never pay for; a test
    # asserts `arcana_cli.main` imports without it.
    from arcana_cli.commands.chat.app import run_chat

    await run_chat(
        reg=reg,
        sm=sm,
        record=record,
        session=session,
        memory_off=no_memory,
        mouse=False if no_mouse else None,
        notes=notes,
    )


def open_chat(
    *, agent: str | None = None, session_id: str | None = None, no_memory: bool = False, no_mouse: bool = False
) -> None:
    """Open the interactive session and run it until the user quits."""
    # A line terminal renders what fails before the session opens; the session is a terminal app.
    r = TtyRenderer()
    run_async(_open_chat(r, agent=agent, session_id=session_id, no_memory=no_memory, no_mouse=no_mouse))


def chat_cmd(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    session_id: str | None = typer.Option(None, "--session", help="Resume a specific session by UUID"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Run stateless — do not load or persist memory"),
    no_mouse: bool = typer.Option(False, "--no-mouse", help=NO_MOUSE_HELP),
) -> None:
    """Start an interactive session with a card-configured agent."""
    open_chat(agent=agent, session_id=session_id, no_memory=no_memory, no_mouse=no_mouse)
