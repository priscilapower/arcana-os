"""The chat controller — all session state plus the submit/slash logic.

:class:`_ChatController` owns the live session and the turn lifecycle. Output
goes through a :class:`~arcana_cli.ui.renderer.Renderer`, so the logic never
touches the terminal: in the session it is a ``TextualRenderer`` over the app,
and tests hand it a recording renderer and ``await`` :meth:`_ChatController.submit`.
The few things only the running app can do (quit, clear the visible
transcript, re-scope input history, run a turn as a cancellable worker) go
through the app bound with :meth:`_ChatController.bind_app`.

The setup wizards (``/agent``, ``/providers``, ``/mcp``; see :mod:`.wizards`)
run inside a turn's worker, so their dialogs can be awaited and Ctrl+C cancels
them. Whatever a wizard raises ends as a transcript note, never the session;
afterwards the session picks up what it changed. One change it deliberately
doesn't pick up on its own: an MCP server added in the session stays out of the
running agent's tools until ``/mcp approve`` admits it.
"""

import asyncio

import typer
from rich.console import Group
from rich.markup import escape
from rich.text import Text
from textual.worker import Worker

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.cards.registry import get_registry
from arcana.memory.federation import MemoryFederation
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.tools import MCPRegistry, ToolConfirmer
from arcana.types.agent import Agent as AgentRecord
from arcana.types.session import MessageRole, Session
from arcana_cli.commands.chat.render import (
    _agent_eyebrow_block,
    _card_table,
    _footer_line,
    _header_block,
    _help_table,
    _live_reply,
    _memory_renderable,
    _note_block,
    _replay_blocks,
    _user_block,
)
from arcana_cli.commands.chat.wizards import Params, Wizard, WizardUsageError, find_wizard, redacted
from arcana_cli.commands.run import AmbiguousAgentError, build_session_runtime, build_world_engine, resolve_agent
from arcana_cli.tui.app import ArcanaApp
from arcana_cli.tui.history import AgentHistory
from arcana_cli.ui.card_panel import card_panel
from arcana_cli.ui.input_model import _SLASH_SUBCOMMANDS, WizardGroup
from arcana_cli.ui.renderer import CANCELLED, Choice, Renderer
from arcana_cli.ui.theme import card_color, dim, err, warn

# Package-internal exports — the chat app builds on these. Declared so the split
# doesn't read as dead code under strict unused-symbol checks.
__all__ = ["_ChatController", "_friendly_error"]

#: The title of the agent picker a bare ``/switch`` opens.
SWITCH_TITLE = "Switch to agent"

#: Why a wizard line carrying a secret is refused.
SECRET_ON_THE_LINE = (
    "A secret can't go on the command line here: the transcript and your input history would keep it. "
    "Leave it off and the wizard asks for it in a hidden prompt."
)


def _keeps_in_history(raw: str) -> bool:
    """Whether a submitted line may go into the input history: not if it carries a secret."""
    return redacted(raw) is None


def _friendly_error(exc: Exception) -> str:
    """Turn a raw turn failure into a short, actionable message.

    Common local-dev failures (provider down, model not pulled, timeout) get a
    hint; anything else falls back to the exception text so nothing is hidden.
    """
    text = str(exc).strip()
    low = text.lower()
    name = type(exc).__name__.lower()
    if "connect" in name or any(
        s in low
        for s in ("connection refused", "failed to establish", "max retries", "all connection attempts failed")
    ):
        return "Couldn't reach the model. Is your provider running?"
    if "timeout" in name or "timed out" in low or "timeout" in low:
        return "The model timed out. It may be busy, or the request was too large."
    if "not found" in low and "model" in low:
        return f"{text}\nThe model may not be installed. Try pulling it first (e.g. `ollama pull <model>`)."
    return f"Error: {text}" if text else f"Error: {type(exc).__name__}"


class _ChatController:
    """Owns the live session and drives one turn at a time.

    The app is a thin shell over this: a submitted input calls
    :meth:`start_turn`, Ctrl+C calls :meth:`cancel_turn` or :meth:`request_exit`.
    A turn runs as an app worker, so anything it awaits may push a dialog and
    wait for the answer, such as a :class:`~arcana.tools.guardrails.ToolConfirmer`
    asking to approve a tool call.

    ``confirmer`` is the interactive approver every runtime agent this
    controller builds (on ``/fresh``, ``/no-memory`` and ``/switch``) is given;
    the caller passes the same one to the first agent. ``connections`` is the
    store ``gw`` resolves models through, and ``tools`` the MCP servers the
    session's agents take their tools from; the caller builds the first agent
    over the same ``tools``.
    """

    def __init__(
        self,
        *,
        renderer: Renderer,
        reg: AgentRegistry,
        gw: ModelGateway,
        sm: SessionManager,
        record: AgentRecord,
        session: Session,
        runtime_agent: RuntimeAgent,
        federation: MemoryFederation | None,
        memory_off: bool,
        connections: ConnectionStore,
        tools: MCPRegistry,
        confirmer: ToolConfirmer | None = None,
    ) -> None:
        self.renderer = renderer
        self.reg = reg
        self._gw = gw
        self._connections = connections
        self.tools = tools
        #: MCP servers added in this session and not yet approved: left out of ``tools``.
        self.unapproved: set[str] = set()
        self._sm = sm
        self.record = record
        self.session = session
        self.runtime_agent = runtime_agent
        self.federation = federation
        self.memory_off = memory_off
        self.confirmer = confirmer
        self.accent = card_color(record.card)
        self.exited = False
        self._app: ArcanaApp | None = None
        self._turn: Worker[None] | None = None

    # -- app wiring -------------------------------------------------------
    def bind_app(self, app: ArcanaApp) -> None:
        """Drive ``app``: scope its input history and ``/switch`` completion, and fill its status line."""
        self._app = app
        app.chat_input.set_history(AgentHistory.for_agent(self.record.id))
        app.chat_input.agent_names = lambda: [r.name for r in self.reg.list()]
        app.chat_input.keep_in_history = _keeps_in_history
        self._refresh_footer()

    def open(self, *, notes: tuple[str, ...] = ()) -> None:
        """Show the session header, any opening ``notes`` (markup), and a resumed session's recent turns."""
        self.append_header()
        for note in notes:
            self._note(note)
        for block in _replay_blocks(self.session, self.record.name, self.accent):
            self.renderer.emit(block)

    def request_exit(self) -> None:
        self.exited = True
        if self._app is not None:
            self._app.exit()

    @property
    def busy(self) -> bool:
        return self._turn is not None and not self._turn.is_finished

    def _refresh_footer(self) -> None:
        if self._app is not None:
            self._app.status_bar.set_idle(_footer_line(self.session.id, memory_off=self.memory_off))

    # -- input handling ---------------------------------------------------
    def start_turn(self, raw: str, text: str | None = None) -> None:
        """Run one submission as a cancellable worker on the bound app.

        ``raw`` is the input as shown (paste placeholders intact); ``text`` is
        what the model receives, defaulting to ``raw``. Blank input, and input
        while a turn is running, is ignored.
        """
        if not raw.strip() or self.busy:
            return
        if self._app is None:
            raise RuntimeError("start_turn needs an app; call bind_app first")
        self._app.chat_input.busy = True
        self._turn = self._app.run_worker(self._guarded_submit(raw, text), name="chat-turn", group="chat-turn")

    def cancel_turn(self) -> None:
        if self._turn is not None and not self._turn.is_finished:
            self._turn.cancel()

    async def _guarded_submit(self, raw: str, text: str | None) -> None:
        """One submission; a failure outside the model turn is shown, not raised into the app."""
        try:
            await self.submit(raw, text)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._note(err(escape(_friendly_error(exc))))
        finally:
            if self._app is not None:
                self._app.chat_input.busy = False

    def append_header(self) -> None:
        self.renderer.emit(_header_block(self.record, memory_off=self.memory_off))

    def _note(self, markup: str) -> None:
        self.renderer.emit(_note_block(markup))

    # -- the turn ---------------------------------------------------------
    async def submit(self, raw: str, text: str | None = None) -> None:
        """Handle one submitted input: a slash command or a model turn.

        ``raw`` is the input as shown; ``text`` (default ``raw``) is what the
        model receives, with any collapsed pastes expanded.
        """
        stripped = raw.strip()
        if not stripped:
            return
        # Slash detection runs on the visible line (paste placeholders intact),
        # so a pasted chunk is never mistaken for a command.
        hidden = redacted(stripped) if stripped.startswith("/") else None
        self.renderer.emit(_user_block(hidden if hidden is not None else raw))
        if hidden is not None:
            self._note(err(SECRET_ON_THE_LINE))
        elif stripped.startswith("/"):
            await self._handle_slash(stripped)
        else:
            await self._run_turn(text if text is not None else raw)

    async def _run_turn(self, message: str) -> None:
        """Stream a reply into a live block that lands in the transcript when it's done.

        Ctrl+C cancels the turn's worker, which raises ``CancelledError`` here;
        anything already streamed is kept and a ``…cancelled`` note is appended.
        Model/memory failures become a friendly one-line error, never a crash.
        """
        try:
            async with self.renderer.stream(
                _agent_eyebrow_block(self.runtime_agent.name, self.accent), render=_live_reply
            ) as sink:
                async for chunk in self.runtime_agent.stream(message, session=self.session):
                    sink.write(chunk)
        except asyncio.CancelledError:
            self._note(dim(" …cancelled"))
            raise
        except Exception as exc:
            self._note(err(escape(_friendly_error(exc))))

    async def _handle_slash(self, cmd: str) -> None:
        name, _, arg = cmd.partition(" ")
        arg = arg.strip()
        if name == "/exit":
            self.request_exit()
        elif name == "/help":
            self.renderer.emit(Group(Text(""), _help_table()))
        elif name == "/card":
            self.renderer.emit(Group(Text(""), _card_table(self.runtime_agent, self.record)))
        elif name == "/memory":
            self.renderer.emit(Group(Text(""), await _memory_renderable(self.federation, self.session)))
        elif name == "/retry":
            await self._retry()
        elif name == "/save":
            self._sm.close(self.session)
            self._note(dim(f"session saved — {str(self.session.id)[:8]}…"))
        elif name == "/clear":
            # Clears what's on screen; the exit replay still prints the whole session.
            if self._app is not None:
                self._app.transcript.clear()
            self.append_header()
        elif name in ("/fresh", "/no-memory"):
            await self._new_session(memory_off=name == "/no-memory")
        elif name == "/switch":
            await self._switch(arg)
        elif name in _SLASH_SUBCOMMANDS:
            await self._wizard(cmd)
        else:
            self._note(err(f"Unknown command {escape(name)}. Type /help."))

    async def _retry(self) -> None:
        last_user = next(
            (m.content for m in reversed(self.session.messages) if m.role == MessageRole.USER),
            None,
        )
        if last_user is None:
            self._note(dim("Nothing to retry yet."))
            return
        # Drop the last exchange so the re-run replaces it rather than appending
        # a duplicate turn.
        while self.session.messages and self.session.messages[-1].role != MessageRole.USER:
            self.session.messages.pop()
        if self.session.messages and self.session.messages[-1].role == MessageRole.USER:
            self.session.messages.pop()
        self._note(dim("retrying…"))
        await self._run_turn(last_user)

    async def _build_runtime(self, record: AgentRecord) -> tuple[RuntimeAgent, MemoryFederation | None]:
        return await build_session_runtime(
            self.reg,
            record,
            self._gw,
            self._sm,
            no_memory=self.memory_off,
            confirmer=self.confirmer,
            tool_registry=self.tools,
        )

    async def _rebuild_runtime(self) -> None:
        """Rebuild the current agent's runtime (its card, model and tools) in the same session."""
        if self.federation is not None:
            await self.federation.aclose()
            self.federation = None
        self.runtime_agent, self.federation = await self._build_runtime(self.record)

    async def _new_session(self, *, memory_off: bool) -> None:
        """Open a fresh session; ``/fresh`` and ``/no-memory`` differ only in mode."""
        self.memory_off = memory_off
        if self.federation is not None:
            await self.federation.aclose()
        self._sm.close(self.session)
        self.session = self._sm.start(self.record.id)
        self.runtime_agent, self.federation = await self._build_runtime(self.record)
        self._refresh_footer()
        mode = "memory off" if memory_off else "memory on"
        self._note(dim(f"new session — {str(self.session.id)[:8]}… ({mode})"))

    async def _pick_agent(self) -> AgentRecord | None:
        """Offer every agent in the two-pane picker, each previewed by its primary card; ``None`` if cancelled."""
        records = self.reg.list()
        if not records:
            self._note(dim("No agents to switch to."))
            return None
        registry = get_registry()
        choices = [
            Choice(
                record.id,
                f"{record.name} · {registry.get(record.card).name}",
                preview=card_panel(registry.get(record.card), registry),
            )
            for record in records
        ]
        picked = await self.renderer.select(choices, title=SWITCH_TITLE, initial=[self.record.id])
        return next((record for record in records if record.id == picked), None)

    async def _switch(self, arg: str) -> None:
        """``/switch <name>`` loads the named agent; a bare ``/switch`` picks one first (Esc changes nothing)."""
        if arg:
            try:
                new_record = resolve_agent(arg, self.reg)
            except AmbiguousAgentError as exc:
                self._note(err(escape(str(exc))))
                return
            if new_record is None:
                self._note(err(f"No agent '{escape(arg)}'."))
                return
        else:
            new_record = await self._pick_agent()
            if new_record is None:
                return
        if not new_record.model:
            self._note(err(f"No model configured for agent '{escape(new_record.name)}'."))
            return
        # /switch is an explicit reroute — record the decision in the routing
        # audit, then load the named agent. Conversation-context transfer is out
        # of scope here; this only re-resolves the agent.
        await build_world_engine(self.reg).route("", explicit_agent=new_record)
        if self.federation is not None:
            await self.federation.aclose()
        self._sm.close(self.session)
        self.record = new_record
        self.accent = card_color(new_record.card)
        self.session = self._sm.start(new_record.id)
        # Re-scope arrow-key history to the agent switched to.
        if self._app is not None:
            self._app.chat_input.set_history(AgentHistory.for_agent(new_record.id))
        self.runtime_agent, self.federation = await self._build_runtime(new_record)
        self._refresh_footer()
        self.append_header()
        self._note(dim(f"(switched to {escape(new_record.name)} · explicit route)"))

    # -- setup wizards ------------------------------------------------------
    async def _wizard(self, line: str) -> None:
        """Run the wizard ``line`` names; any failure (or cancel) is a note, and the session carries on."""
        wizard, args = find_wizard(line)
        if wizard is None:
            command = line.split(" ", 1)[0]
            self._note(err(f"Usage: {command} {'|'.join(_SLASH_SUBCOMMANDS[command])} [options]"))
            return
        try:
            params = wizard.parse(args)
        except WizardUsageError as exc:
            self._note(err(escape(f"{wizard.name}: {exc}")))
            self._note(dim(f"It takes the options of {wizard.one_shot} (see {wizard.one_shot} --help)."))
            return
        mcp_before = self._mcp_servers_on_disk()
        succeeded = False
        try:
            await wizard.run(self.renderer, params)
            succeeded = True
        except typer.Abort:
            self._note(dim(CANCELLED))
        except typer.Exit as exc:
            # The wizard said why before it exited; a clean exit (nothing to do) counts as done.
            succeeded = exc.exit_code == 0
        except asyncio.CancelledError:
            self._note(dim(" …cancelled"))
            raise
        except Exception as exc:
            self._note(err(escape(f"{wizard.name} failed: {exc}")))
        await self._after_wizard(wizard, params, succeeded=succeeded, mcp_before=mcp_before)

    async def _after_wizard(self, wizard: Wizard, params: Params, *, succeeded: bool, mcp_before: set[str]) -> None:
        """Pick up what ``wizard`` changed: agents, provider connections, or MCP servers."""
        if wizard.command is WizardGroup.AGENT:
            await self._reload_agent()
        elif wizard.command is WizardGroup.PROVIDERS:
            # The next model call re-reads the connections and reconnects with them.
            self._connections.reload()
            await self._gw.aclose()
        elif wizard.command is WizardGroup.MCP:
            approved = params.get("name") if succeeded and wizard.admits_server else None
            await self._settle_tools(approved=approved if isinstance(approved, str) else None, before=mcp_before)

    async def _reload_agent(self) -> None:
        """Rebuild the runtime when a wizard edited the current agent; say so when it deleted it."""
        fresh = self.reg.get(self.record.id)
        if fresh is None or fresh.is_archived:
            self._note(warn(f"'{escape(self.record.name)}' was deleted; this session runs on until you /switch."))
            return
        if fresh == self.record:
            return
        self.record = fresh
        self.accent = card_color(fresh.card)
        await self._rebuild_runtime()
        self._refresh_footer()
        self._note(dim(f"(reloaded {escape(fresh.name)})"))

    def _mcp_servers_on_disk(self) -> set[str]:
        registry = MCPRegistry(connections_file=self.tools.connections_file)
        registry.load()
        return {server.name for server in registry.list_servers()}

    async def _settle_tools(self, *, approved: str | None, before: set[str]) -> None:
        """Keep the session's MCP tools in step with an ``/mcp`` wizard.

        A server the wizard added is held back until approved; one it removed is
        dropped at once. After ``/mcp approve`` of server ``approved``, the tools
        are re-read from every server, still leaving out the ones added here and
        not yet approved.
        """
        after = self._mcp_servers_on_disk()
        added, removed = after - before, before - after
        for name in sorted(added):
            self.unapproved.add(name)
            self._note(
                dim(f"'{escape(name)}' isn't in this session's tools until you approve it: /mcp approve {name} --all")
            )
        if approved is not None:
            admitted = approved in self.unapproved
            self.unapproved.discard(approved)
            fresh = MCPRegistry(connections_file=self.tools.connections_file)
            fresh.load()
            self.tools = fresh.without(self.unapproved)
            await self._rebuild_runtime()
            if admitted:
                self._note(dim(f"'{escape(approved)}' is now in this session's tools."))
        elif removed:
            self.unapproved -= removed
            self.tools = self.tools.without(removed)
            await self._rebuild_runtime()
