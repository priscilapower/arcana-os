"""The chat controller — all session state plus the submit/slash logic.

:class:`_ChatController` owns the live session, the transcript, and the turn
lifecycle; the full-screen app is a thin shell over it. Because it never touches
the terminal directly, tests drive it by ``await``-ing :meth:`_ChatController.submit`
and asserting on ``transcript.plain_text()``.
"""

import asyncio

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from rich.console import Group
from rich.markup import escape
from rich.text import Text

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory.federation import MemoryFederation
from arcana.models.gateway import ModelGateway
from arcana.types.agent import Agent as AgentRecord
from arcana.types.session import MessageRole, Session
from arcana_cli.commands.chat.editor import _agent_history, _PasteRegistry
from arcana_cli.commands.chat.render import (
    _agent_eyebrow_block,
    _card_table,
    _header_block,
    _help_table,
    _memory_renderable,
    _note_block,
    _render_reply,
    _Transcript,
    _user_block,
)
from arcana_cli.commands.run import build_session_runtime, find_agent
from arcana_cli.ui.theme import TXT3, card_color, dim, err

# Package-internal exports — the app layout builds on these. Declared so the
# split doesn't read as dead code under strict unused-symbol checks.
__all__ = ["_ChatController", "_H_PAD", "_friendly_error"]

# Horizontal gutter (columns) on each side of the whole chat, so nothing is
# glued to the terminal edge. The transcript width math (below) and the app's
# padded layout both subtract it, so it lives here where both can import it.
_H_PAD = 2


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

    The full-screen app is a thin shell over this: key bindings call
    :meth:`start_turn`/:meth:`cancel_turn`, and the layout reads
    :attr:`transcript`. Tests skip the shell and ``await`` :meth:`submit` directly,
    asserting on ``transcript.plain_text()``.
    """

    def __init__(
        self,
        *,
        reg: AgentRegistry,
        gw: ModelGateway,
        sm: SessionManager,
        record: AgentRecord,
        session: Session,
        runtime_agent: RuntimeAgent,
        federation: MemoryFederation | None,
        memory_off: bool,
    ) -> None:
        self.reg = reg
        self._gw = gw
        self._sm = sm
        self.record = record
        self.session = session
        self.runtime_agent = runtime_agent
        self.federation = federation
        self.memory_off = memory_off
        self.accent = card_color(record.card)
        self.transcript = _Transcript()
        self.pastes = _PasteRegistry()
        self.exited = False
        self._app: Application[None] | None = None
        self._input_buffer: Buffer | None = None
        self._turn_task: asyncio.Task[None] | None = None

    # -- app wiring -------------------------------------------------------
    def bind_app(self, app: Application[None], input_buffer: Buffer) -> None:
        self._app = app
        self._input_buffer = input_buffer

    def _invalidate(self) -> None:
        if self._app is not None:
            self._app.invalidate()

    def content_width(self) -> int:
        """Width the transcript renders to — terminal columns minus the gutters."""
        cols = self._app.output.get_size().columns if self._app is not None else 80
        return max(20, cols - 2 * _H_PAD)

    def request_exit(self) -> None:
        self.exited = True
        if self._app is not None:
            self._app.exit()

    @property
    def busy(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    # -- input handling ---------------------------------------------------
    def on_accept(self, buff: Buffer) -> bool:
        """Buffer accept handler: schedule the turn, then clear the input.

        Returns ``True`` (keep the text) while a turn is streaming so a stray
        Enter can't lose what you were typing; otherwise ``False`` to clear.
        """
        if self.busy:
            return True
        self.start_turn(buff.text)
        return False

    def start_turn(self, text: str) -> None:
        """Run one submission as a cancellable background task on the app loop."""
        if not text.strip():
            return
        self._turn_task = asyncio.ensure_future(self._guarded_submit(text))

    def cancel_turn(self) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()

    async def _guarded_submit(self, text: str) -> None:
        try:
            await self.submit(text)
        except asyncio.CancelledError:
            pass
        finally:
            self._invalidate()

    def append_header(self) -> None:
        self.transcript.append(_header_block(self.record, memory_off=self.memory_off))

    def _note(self, markup: str) -> None:
        self.transcript.append(_note_block(markup))

    # -- the turn ---------------------------------------------------------
    async def submit(self, raw: str) -> None:
        """Handle one submitted line: a slash command or a model turn."""
        stripped = raw.strip()
        if not stripped:
            return
        # Slash detection runs on the visible line (paste placeholders intact),
        # so a pasted chunk is never mistaken for a command.
        self.transcript.append(_user_block(raw))
        self._invalidate()
        if stripped.startswith("/"):
            await self._handle_slash(stripped)
        else:
            # Expand paste placeholders, then reset the registry — placeholders
            # belong to the message that carried them and must not leak forward.
            message = self.pastes.expand(raw)
            self.pastes.clear()
            await self._run_turn(message)
        self._invalidate()

    async def _run_turn(self, message: str) -> None:
        """Stream a reply into the transcript, updating it token by token.

        Ctrl+C cancels the streaming task, which raises ``CancelledError`` here;
        anything already streamed is kept and a ``…cancelled`` note is appended.
        Model/memory failures become a friendly one-line error, never a crash.
        """
        self.transcript.append(_agent_eyebrow_block(self.runtime_agent.name, self.accent))
        self.transcript.append(Text("…thinking", style=f"italic {TXT3}"))
        self._invalidate()
        parts: list[str] = []

        try:
            async for chunk in self.runtime_agent.stream(message, session=self.session):
                parts.append(chunk)
                self.transcript.update_last(_render_reply("".join(parts)))
                self._invalidate()
            self.transcript.update_last(_render_reply("".join(parts)))
        except asyncio.CancelledError:
            self.transcript.update_last(_render_reply("".join(parts)) if parts else Text(""))
            self._note(dim(" …cancelled"))
            raise
        except Exception as exc:
            self.transcript.update_last(_render_reply("".join(parts)) if parts else Text(""))
            self._note(err(_friendly_error(exc)))

        self._invalidate()

    async def _handle_slash(self, cmd: str) -> None:
        name, _, arg = cmd.partition(" ")
        arg = arg.strip()
        if name == "/exit":
            self.request_exit()
        elif name == "/help":
            self.transcript.append(Group(Text(""), _help_table()))
        elif name == "/card":
            self.transcript.append(Group(Text(""), _card_table(self.runtime_agent, self.record)))
        elif name == "/memory":
            self.transcript.append(Group(Text(""), await _memory_renderable(self.federation, self.session)))
        elif name == "/retry":
            await self._retry()
        elif name == "/save":
            self._sm.close(self.session)
            self._note(dim(f"session saved — {str(self.session.id)[:8]}…"))
        elif name == "/clear":
            self.transcript.clear()
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

    async def _new_session(self, *, memory_off: bool) -> None:
        """Open a fresh session; ``/fresh`` and ``/no-memory`` differ only in mode."""
        self.memory_off = memory_off
        if self.federation is not None:
            await self.federation.aclose()
        self._sm.close(self.session)
        self.session = self._sm.start(self.record.id)
        self.runtime_agent, self.federation = await build_session_runtime(
            self.reg, self.record, self._gw, self._sm, no_memory=memory_off
        )
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
        if self.federation is not None:
            await self.federation.aclose()
        self._sm.close(self.session)
        self.record = new_record
        self.accent = card_color(new_record.card)
        self.session = self._sm.start(new_record.id)
        # Re-scope arrow-key history to the agent switched to.
        if self._input_buffer is not None:
            self._input_buffer.history = _agent_history(new_record.id)
        self.runtime_agent, self.federation = await build_session_runtime(
            self.reg, new_record, self._gw, self._sm, no_memory=self.memory_off
        )
        self.append_header()
        self._note(dim("(World re-routing deferred - loaded the named agent directly.)"))
