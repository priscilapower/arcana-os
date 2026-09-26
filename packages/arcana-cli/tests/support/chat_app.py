"""The chat app under Pilot: a running :class:`ChatApp` over a controller, and helpers to drive it.

:func:`chat_session` runs the real chat app headless with a controller from
:func:`~tests.support.chat.make_controller` rendering into it, so a test types
into the real input and reads the real transcript.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from arcana.types.agent import Agent as AgentRecord
from arcana_cli.commands.chat.app import ChatApp
from arcana_cli.commands.chat.controller import _ChatController
from arcana_cli.ui.renderer.textual_renderer import TextualRenderer
from tests.support.chat import make_controller
from tests.support.tui import TuiHarness, arcana_pilot


class ChatSession:
    """A running chat app, its controller, and helpers to drive it."""

    def __init__(self, h: TuiHarness, controller: _ChatController) -> None:
        self.h = h
        self.app = h.app
        self.pilot = h.pilot
        self.c = controller

    async def send(self, text: str) -> None:
        """Type ``text`` and press Enter (the turn starts; it may still be running)."""
        await self.pilot.press(*text, "enter")

    async def enter(self, line: str) -> None:
        """Put ``line`` in the input box and press Enter, as if typed (no completion menu pops)."""
        self.app.chat_input.load_text(line)
        await self.pilot.pause()
        await self.pilot.press("enter")

    async def answer(self, text: str) -> None:
        """Type ``text`` into the dialog on top and press Enter."""
        if text:
            await self.pilot.press(*text)
        await self.pilot.press("enter")

    async def settle(self) -> None:
        """Let the running turn finish."""
        for _ in range(100):
            await self.pilot.pause()
            if not self.c.busy:
                return
        raise AssertionError("the turn never finished")

    async def until(self, check: Any, what: str) -> None:
        for _ in range(100):
            await self.pilot.pause(0.01)
            if check():
                return
        raise AssertionError(f"never happened: {what}")

    @property
    def input_focused(self) -> bool:
        return self.app.focused is self.app.chat_input

    def retained(self) -> str:
        return self.h.retained_text()

    def live_text(self) -> str:
        """The live block as drawn on screen right now."""
        live = self.app.live
        return "\n".join(live.render_line(y).text for y in range(live.size.height))


@asynccontextmanager
async def chat_session(home: Path, record: AgentRecord, runtime: Any) -> AsyncIterator["ChatSession"]:
    app = ChatApp()
    controller = make_controller(home, record, runtime, renderer=TextualRenderer(app))
    app.controller = controller
    async with arcana_pilot(app=app) as h:
        yield ChatSession(h, controller)
