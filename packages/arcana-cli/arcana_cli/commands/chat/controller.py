"""The chat controller — all session state plus the submit/slash logic.

:class:`_ChatController` owns the live session and the turn lifecycle. Output
goes through a :class:`~arcana_cli.ui.renderer.Renderer`, so the logic never
touches the terminal: in the session it is a ``TextualRenderer`` over the app,
and tests hand it a recording renderer and ``await`` :meth:`_ChatController.submit`.
The few things only the running app can do (quit, clear the visible
transcript, re-scope input history, run a turn as a cancellable worker) go
through the app bound with :meth:`_ChatController.bind_app`.
"""

import asyncio

from rich.console import Group
from rich.markup import escape
from rich.text import Text
from textual.worker import Worker

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory.federation import MemoryFederation
from arcana.models.gateway import ModelGateway
from arcana.tools import ToolConfirmer
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
from arcana_cli.commands.run import build_session_runtime, build_world_engine, find_agent
from arcana_cli.tui.app import ArcanaApp
from arcana_cli.tui.history import AgentHistory
from arcana_cli.ui.renderer import Renderer
from arcana_cli.ui.theme import card_color, dim, err

# Package-internal exports — the chat app builds on these. Declared so the split
# doesn't read as dead code under strict unused-symbol checks.
__all__ = ["_ChatController", "_friendly_error"]


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
    the caller passes the same one to the first agent.
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
        confirmer: ToolConfirmer | None = None,
    ) -> None:
        self.renderer = renderer
        self.reg = reg
        self._gw = gw
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
        self.renderer.emit(_user_block(raw))
        if stripped.startswith("/"):
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
            self.reg, record, self._gw, self._sm, no_memory=self.memory_off, confirmer=self.confirmer
        )

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

    async def _switch(self, arg: str) -> None:
        new_record = find_agent(arg, self.reg) if arg else None
        if not arg:
            self._note(err("Usage: /switch <name>"))
            return
        if new_record is None:
            self._note(err(f"No agent '{escape(arg)}'."))
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
