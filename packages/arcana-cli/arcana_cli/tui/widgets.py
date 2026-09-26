"""The app's standing widgets: the transcript, the live stream block and the status bar."""

from rich.console import Group, RenderableType
from rich.spinner import Spinner
from rich.text import Text
from textual.widget import Widget
from textual.widgets import RichLog, Static

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
    """The block a stream grows into, shown only while a stream is open."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._prefix: RenderableType | None = None
        self._text = Text()

    def open(self, prefix: RenderableType | None) -> None:
        """Start a new block led by ``prefix``, and show it."""
        self._prefix = prefix
        self._text = Text()
        self.add_class("-active")
        self._refresh_content()

    def feed(self, chunk: str) -> None:
        """Append ``chunk`` to the open block."""
        self._text.append(chunk)
        self._refresh_content()

    def close(self) -> RenderableType:
        """Hide the block and return its finished content."""
        finished = self.content_renderable
        self.remove_class("-active")
        self._prefix = None
        self._text = Text()
        self.update("")
        return finished

    @property
    def content_renderable(self) -> RenderableType:
        """The prefix and the streamed text so far, as one renderable."""
        if self._prefix is None:
            return self._text.copy()
        if isinstance(self._prefix, str | Text):
            prefix = Text.from_markup(self._prefix) if isinstance(self._prefix, str) else self._prefix
            return Text.assemble(prefix, self._text)
        return Group(self._prefix, self._text.copy())

    def _refresh_content(self) -> None:
        self.update(self.content_renderable)


class StatusBar(Widget):
    """One line under the transcript: a spinner and the innermost open status, or blank.

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

    def render(self) -> RenderableType:
        if not self._open:
            return Text()
        self._spinner.update(text=Text.from_markup(self.messages[-1]))
        return self._spinner
