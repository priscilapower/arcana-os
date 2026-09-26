"""``ArcanaApp`` — the interactive session's Textual app shell — and its base, ``ArcanaBaseApp``.

The session app is a transcript, a live block for streamed output, the chat input
(focused on start) and a status bar, with question dialogs pushed on top as
modal screens. It owns the platform
defaults every screen inside it inherits:

* **Inline where the terminal allows it.** :meth:`ArcanaApp.run_inline` runs the
  app under the shell prompt on macOS and Linux. Textual has no inline driver on
  Windows, so there it runs full-screen (a one-line notice says so the first
  time).
* **Mouse on by default.** Wheel scrolling and clickable dialogs; ``ui.mouse =
  false`` in ``config.json`` (or ``mouse=False``) restores native drag-select.
* **The transcript survives exit.** The inline region is cleared when the app
  exits and every retained transcript block is printed to stdout through a
  Rich console, so the session lands in terminal scrollback. The replay
  is capped at ``ARCANA_TUI_REPLAY_BLOCKS`` blocks, with a note for the rest.

The app runs on the caller's event loop (``await app.run_inline()`` inside the
one :func:`asyncio.run` of :func:`arcana_cli._async.run_async`); it never starts
a loop of its own.

:class:`ArcanaBaseApp` holds what every Arcana app shares — the generated
stylesheet and theme, and :meth:`ArcanaBaseApp.run_here`, the inline-where-possible
run on the current loop — so a short-lived app (the one-shot card picker) looks
and runs like the session.
"""

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import TypeVar

from rich.console import Console, RenderableType
from rich.text import Text
from textual.app import App, ComposeResult

from arcana_cli.constants import ARCANA_HOME
from arcana_cli.tui.chat_input import ChatInput, ChatInputPanel
from arcana_cli.tui.config import REPLAY_BLOCKS, load_ui_config
from arcana_cli.tui.theme_tcss import ARCANA_TCSS, ARCANA_THEME
from arcana_cli.tui.widgets import LiveBlock, StatusBar, Transcript
from arcana_cli.ui.theme import MARKDOWN_THEME, dim

_R = TypeVar("_R")

#: Printed before the first full-screen session on Windows.
FULLSCREEN_NOTICE = "Arcana runs full-screen on Windows; the session transcript is printed when you exit."

#: Marks that :data:`FULLSCREEN_NOTICE` has been shown, relative to ``ARCANA_HOME``.
_FULLSCREEN_NOTICE_MARKER = Path(".fullscreen-notice-shown")


def replay_tail(blocks: list[RenderableType], limit: int) -> list[RenderableType]:
    """The last ``limit`` blocks, led by a note counting any earlier ones left out."""
    shown = blocks[-limit:] if limit > 0 else []
    hidden = len(blocks) - len(shown)
    if not hidden:
        return list(shown)
    note = Text.from_markup(dim(f"… {hidden} earlier block{'s' if hidden != 1 else ''} not shown"))
    return [note, *shown]


class ArcanaBaseApp(App[_R]):
    """An app on Arcana's stylesheet and theme, run on the caller's loop with :meth:`run_here`."""

    CSS = ARCANA_TCSS
    ENABLE_COMMAND_PALETTE = False

    def __init__(self) -> None:
        super().__init__()
        self.register_theme(ARCANA_THEME)
        self.theme = ARCANA_THEME.name
        self.console.push_theme(MARKDOWN_THEME)

    async def run_here(self, *, mouse: bool | None = None) -> _R | None:
        """Run the app on the current event loop and return its result.

        Inline on POSIX, full-screen on Windows (Textual has no inline driver
        there). ``mouse=None`` reads ``ui.mouse`` from ``config.json`` (default on).

        Textual installs its eager task factory on the running loop; the loop's
        previous factory is put back when the app exits, so the rest of the
        invocation runs on the loop it started with.
        """
        if mouse is None:
            mouse = load_ui_config(ARCANA_HOME).mouse
        loop = asyncio.get_running_loop()
        task_factory = loop.get_task_factory()
        try:
            return await self.run_async(inline=sys.platform != "win32", mouse=mouse)
        finally:
            loop.set_task_factory(task_factory)


class ArcanaApp(ArcanaBaseApp[None]):
    """The interactive session's app shell.

    ``transcript``, ``live``, ``chat_panel`` (holding ``chat_input``) and
    ``status_bar`` are the standing widgets; a
    :class:`~arcana_cli.ui.renderer.textual_renderer.TextualRenderer` writes to
    them and pushes its question dialogs onto the screen stack.
    """

    def __init__(self) -> None:
        super().__init__()
        self.transcript = Transcript(id="transcript")
        self.live = LiveBlock(id="live")
        self.chat_input = ChatInput(id="chat-input")
        self.chat_panel = ChatInputPanel(self.chat_input, id="chat")
        self.status_bar = StatusBar(id="status")

    def compose(self) -> ComposeResult:
        yield self.transcript
        yield self.live
        yield self.chat_panel
        yield self.status_bar

    def on_mount(self) -> None:
        self.chat_input.focus()

    async def run_inline(
        self,
        *,
        mouse: bool | None = None,
        console: Console | None = None,
        replay_limit: int = REPLAY_BLOCKS,
    ) -> None:
        """Run the app with :meth:`run_here`, then replay the transcript to ``console``.

        ``console`` defaults to a stdout console, created at call time. The app's
        console and the default replay console both carry the Markdown theme, so
        a reply looks the same in the session and in scrollback.
        """
        out = console if console is not None else replay_console()
        if sys.platform == "win32":
            _notice_fullscreen_once(out, ARCANA_HOME)
        try:
            await self.run_here(mouse=mouse)
        finally:
            for block in replay_tail(self.transcript.retained, replay_limit):
                out.print(block)


def replay_console() -> Console:
    """The stdout console a session's transcript is replayed to when it ends.

    It carries the Markdown theme, as the app's own console does, so a reply
    looks the same in the session and in scrollback.
    """
    return Console(theme=MARKDOWN_THEME)


def _notice_fullscreen_once(console: Console, home: Path) -> None:
    """Print :data:`FULLSCREEN_NOTICE` unless it was shown before under ``home``.

    Without an ``ARCANA_HOME`` to remember it in, the notice shows every time.
    """
    marker = home / _FULLSCREEN_NOTICE_MARKER
    if marker.exists():
        return
    console.print(dim(FULLSCREEN_NOTICE))
    if home.is_dir():
        with contextlib.suppress(OSError):
            marker.touch()
