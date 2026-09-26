"""The app's standing widgets: the transcript, the live stream block and the status bar."""

from rich.console import Group, RenderableType
from rich.spinner import Spinner
from rich.text import Text
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import RichLog, Static

from arcana_cli.tui.config import STREAM_FPS
from arcana_cli.ui.renderer.port import StreamRender
from arcana_cli.ui.theme import ACCENT

#: Spinner frames per second while a status is showing.
_SPINNER_FPS = 12


class Transcript(RichLog):
    """The session's scrollable output, which also keeps every block it shows.

    :meth:`append` is the entry point: it writes the renderable and retains it in
    :attr:`retained`, in order, so the whole session can be printed to the
    terminal when the app exits. :attr:`retained` is the session record, so a
    ``clear()`` of the visible log leaves it intact. Writing through
    :meth:`~textual.widgets.RichLog.write` directly shows a block without
    recording it.
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(wrap=True, min_width=1, id=id)
        self.retained: list[RenderableType] = []

    def append(self, renderable: RenderableType) -> None:
        """Show ``renderable`` at the bottom of the log and retain it for the exit replay."""
        self.retained.append(renderable)
        self.write(renderable, expand=True)


class LiveBlock(Static):
    """The block a stream grows into, shown only while a stream is open.

    Chunks are buffered as they arrive and the block redraws on a timer, at most
    ``fps`` times a second and only when something new came in, so a stream that
    outpaces the terminal costs one redraw per frame rather than one per chunk.
    With a ``render`` function each redraw re-renders the whole text (so partial
    Markdown re-flows correctly); without one the text is shown as it came.
    """

    def __init__(self, *, fps: int = STREAM_FPS, id: str | None = None) -> None:
        super().__init__(id=id)
        self._fps = fps
        self._prefix: RenderableType | None = None
        self._format: StreamRender | None = None
        self._chunks: list[str] = []
        self._dirty = False
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / self._fps, self._redraw_if_dirty, pause=True)

    def open(self, prefix: RenderableType | None, render: StreamRender | None = None) -> None:
        """Start a new block led by ``prefix`` and formatted by ``render``, and show it."""
        self._prefix = prefix
        self._format = render
        self._chunks = []
        self._dirty = False
        self.add_class("-active")
        self.update(self.content_renderable)
        if self._timer is not None:
            self._timer.resume()

    def feed(self, chunk: str) -> None:
        """Append ``chunk`` to the open block; it shows at the next redraw."""
        self._chunks.append(chunk)
        self._dirty = True

    def close(self) -> RenderableType:
        """Hide the block and return its finished content.

        With a ``render`` function, a block that received no text finishes as
        its prefix alone.
        """
        if self._timer is not None:
            self._timer.pause()
        if self._format is not None and not self.text:
            finished: RenderableType = self._lead() or Text()
        else:
            finished = self.content_renderable
        self.remove_class("-active")
        self._prefix = None
        self._format = None
        self._chunks = []
        self._dirty = False
        self.update("")
        return finished

    @property
    def text(self) -> str:
        """Everything streamed into the open block so far."""
        return "".join(self._chunks)

    @property
    def content_renderable(self) -> RenderableType:
        """The prefix and the streamed text so far, as one renderable."""
        lead = self._lead()
        if self._format is not None:
            body = self._format(self.text)
            return body if lead is None else Group(lead, body)
        text = Text(self.text)
        if lead is None:
            return text
        if isinstance(lead, Text):
            return Text.assemble(lead, text)
        return Group(lead, text)

    def _lead(self) -> RenderableType | None:
        """The prefix as a renderable: a string prefix is Rich markup."""
        if isinstance(self._prefix, str):
            return Text.from_markup(self._prefix)
        return self._prefix

    def _redraw_if_dirty(self) -> None:
        if self._dirty:
            self._dirty = False
            self.update(self.content_renderable)


class StatusBar(Widget):
    """One line under the transcript: a spinner and the innermost open status, or the idle line.

    The idle line (blank until :meth:`set_idle`) shows whenever no status is
    open; a session puts its standing facts and key hints there.

    Statuses nest: :meth:`push` shows a message and returns a handle, :meth:`pop`
    removes that message again and the newest one still open (if any) shows once
    more. Handles, not message text, identify a status, so two concurrent
    statuses with the same text stay independent.
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._open: dict[int, str] = {}
        self._next_handle = 0
        self._spinner = Spinner("dots", style=ACCENT)
        self._idle: RenderableType = Text()

    @property
    def messages(self) -> tuple[str, ...]:
        """The open statuses, oldest first."""
        return tuple(self._open.values())

    def push(self, msg: str) -> int:
        """Show ``msg``; returns the handle that :meth:`pop` takes."""
        handle = self._next_handle
        self._next_handle += 1
        self._open[handle] = msg
        self.auto_refresh = 1 / _SPINNER_FPS
        self.refresh()
        return handle

    def pop(self, handle: int) -> None:
        """Remove the status ``handle`` identifies (a no-op if it's already gone)."""
        self._open.pop(handle, None)
        if not self._open:
            self.auto_refresh = None
        self.refresh()

    def set_idle(self, renderable: RenderableType) -> None:
        """Show ``renderable`` whenever no status is open."""
        self._idle = renderable
        self.refresh()

    def render(self) -> RenderableType:
        if not self._open:
            return self._idle
        self._spinner.update(text=Text.from_markup(self.messages[-1]))
        return self._spinner
