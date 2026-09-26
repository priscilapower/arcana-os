"""The chat session's app and its run: :class:`ChatApp` and :func:`run_chat`.

:class:`ChatApp` is the interactive :class:`~arcana_cli.tui.app.ArcanaApp` with
the chat's keys: a submitted input starts a turn, Ctrl+C cancels a running
turn (or quits when idle), and Ctrl+D quits at an empty prompt. Everything else
is the :class:`~.controller._ChatController` it drives.

:func:`run_chat` opens the session on the caller's event loop: the model
gateway, the runtime agent and its memory, the app run inline, then the resume
hint. Whatever happens inside the app, the session is closed, the memory
federation is closed and the terminal is restored before it returns. While the
app runs, a stdio MCP server's stderr goes to ``logs/mcp-stdio.log`` under
``ARCANA_HOME`` instead of the terminal the app draws on.
"""

import contextlib
import os
from collections.abc import Generator
from pathlib import Path

import typer
from rich.console import Console
from textual.actions import SkipAction
from textual.binding import Binding

from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory.federation import MemoryFederation
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.tools import MCPRegistry
from arcana.tools.adapters.mcp import stdio_errlog
from arcana.types.agent import Agent as AgentRecord
from arcana.types.session import Session
from arcana_cli._render import EXIT_ERROR
from arcana_cli.commands.chat.controller import _ChatController
from arcana_cli.commands.run import build_session_runtime
from arcana_cli.constants import ARCANA_HOME
from arcana_cli.tui.app import ArcanaApp, replay_console
from arcana_cli.tui.chat_input import ChatInput
from arcana_cli.ui.renderer.textual_renderer import TextualRenderer
from arcana_cli.ui.theme import dim


class ChatApp(ArcanaApp):
    """The chat session's app: :class:`ArcanaApp` plus the chat's keys, driving a controller.

    Ctrl+C and Ctrl+D are priority bindings, so they reach the session before
    the input box (which binds Ctrl+C to copy and Ctrl+D to delete forward). A
    Ctrl+D with text in the box falls through to the box.
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel / quit", show=False, priority=True),
        Binding("ctrl+d", "end_of_input", "Quit", show=False, priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.controller: _ChatController | None = None

    def on_mount(self) -> None:
        # Textual runs ArcanaApp.on_mount (focusing the input) itself: it calls
        # every class's handler along the MRO, so this one never calls super().
        if self.controller is not None:
            self.controller.bind_app(self)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        if self.controller is not None:
            self.controller.start_turn(event.raw, event.text)

    def action_interrupt(self) -> None:
        """Ctrl+C: cancel the running turn, or quit when there is none."""
        if self.controller is None:
            self.exit()
        elif self.controller.busy:
            self.controller.cancel_turn()
        else:
            self.controller.request_exit()

    def action_end_of_input(self) -> None:
        """Ctrl+D: quit at an empty, idle prompt; otherwise the key goes to the input box."""
        if self.chat_input.text or (self.controller is not None and self.controller.busy):
            raise SkipAction()
        if self.controller is None:
            self.exit()
        else:
            self.controller.request_exit()


#: Where a stdio MCP server's stderr goes while the session runs, relative to ``ARCANA_HOME``.
STDIO_ERRLOG = Path("logs") / "mcp-stdio.log"


@contextlib.contextmanager
def _stdio_errlog_to(path: Path) -> Generator[None]:
    """Send stdio MCP servers' stderr to ``path`` for the block (appending), off the terminal.

    If the log can't be opened the servers' stderr is dropped rather than
    written over the screen.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        sink = path.open("a", encoding="utf-8")
    except OSError:
        sink = Path(os.devnull).open("w", encoding="utf-8")
    with sink:
        token = stdio_errlog.set(sink)
        try:
            yield
        finally:
            stdio_errlog.reset(token)


async def run_chat(
    *,
    reg: AgentRegistry,
    sm: SessionManager,
    record: AgentRecord,
    session: Session | None,
    memory_off: bool,
    mouse: bool | None,
    notes: tuple[str, ...] = (),
    console: Console | None = None,
) -> None:
    """Run the chat session with ``record`` until the user quits.

    ``session`` is a loaded session of ``record``'s to resume; ``None`` starts
    a new one. ``mouse`` is passed to
    :meth:`~arcana_cli.tui.app.ArcanaApp.run_inline` (``None`` reads
    ``ui.mouse``). ``notes`` (Rich markup) open the transcript under the header.
    The transcript replay and then the resume hint print to ``console``
    (default: :func:`~arcana_cli.tui.app.replay_console`, stdout).

    A crash inside the app still closes the session and the federation; it
    then exits with :data:`~arcana_cli._render.EXIT_ERROR` after Textual has
    restored the terminal and printed the traceback.
    """
    out = console if console is not None else replay_console()
    session = session if session is not None else sm.start(record.id)
    app = ChatApp()
    federation: MemoryFederation | None = None
    controller: _ChatController | None = None
    store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
    tools = MCPRegistry(connections_file=ARCANA_HOME / "connections" / "mcps.json")
    tools.load()
    try:
        async with ModelGateway(connections=store) as gw:
            runtime_agent, federation = await build_session_runtime(
                reg, record, gw, sm, no_memory=memory_off, tool_registry=tools
            )
            controller = _ChatController(
                renderer=TextualRenderer(app),
                reg=reg,
                gw=gw,
                sm=sm,
                record=record,
                session=session,
                runtime_agent=runtime_agent,
                federation=federation,
                memory_off=memory_off,
                connections=store,
                tools=tools,
            )
            controller.open(notes=notes)
            app.controller = controller
            with _stdio_errlog_to(ARCANA_HOME / STDIO_ERRLOG):
                await app.run_inline(mouse=mouse, console=out)
    finally:
        # The controller swaps its session and federation on /fresh and /switch;
        # close whichever are current.
        if controller is not None:
            session, federation = controller.session, controller.federation
        with contextlib.suppress(Exception):
            sm.close(session)
        if federation is not None:
            await federation.aclose()
    out.print(dim(f"session: {str(session.id)[:8]}  ·  resume with  --session {session.id}"))
    if app.return_code:
        raise typer.Exit(EXIT_ERROR)
