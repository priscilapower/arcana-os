"""The chat input: a multi-line prompt box on Textual's ``TextArea``.

:class:`ChatInput` is the editor; :class:`ChatInputPanel` stacks it with the two
widgets it drives, the slash-command :class:`CompletionMenu` and the Ctrl+R
:class:`SearchBar`. The toolkit-neutral rules (when Enter submits, when a paste
collapses, the slash-command names) come from :mod:`arcana_cli.ui.input_model`.

Keys:

* **Enter** submits, posting :class:`ChatInput.Submitted` with the pasted text
  expanded. An odd run of trailing ``\\`` before the cursor makes Enter a
  newline instead, dropping the final ``\\``. With :attr:`ChatInput.busy` set,
  Enter keeps the text.
* **Ctrl+J** inserts a newline on every terminal. **Alt+Enter** and
  **Shift+Enter** do too where the terminal speaks the kitty keyboard protocol;
  elsewhere the terminal sends Alt+Enter as a plain Enter, which submits.
* A bracketed paste of four lines or more shows as ``[pasted N lines]``; the
  full text is restored on submit. Ctrl+Z undoes a paste in one step.
* A leading ``/`` pops the completion menu while typing: command names, a
  group's actions (``/agent e`` → ``edit``), a command's options after ``-``,
  and agent names where an argument names an agent (``/switch``, ``/agent edit``).
  Tab / Down step forward through it and Shift+Tab / Up back, inserting the
  highlighted item; Esc closes it and restores the text.
* Up on the first row and Down on the last walk the agent's history, keeping
  the draft. Recalled text never pops the menu. The ghost suggestion comes from
  history and → accepts it.
* Ctrl+R searches history backwards: typing narrows, Ctrl+R again finds an
  older match, Enter accepts it without submitting, Esc restores the text.
* Ctrl+L repaints the screen.

Implementation notes carried from Textual's internals:

* ``TextArea.history`` is the undo stack; the input history is :attr:`ChatInput.recall`.
* Textual calls ``_on_key`` and ``_on_paste`` on every class in the MRO itself,
  ours first, so they never call ``super()``; ``prevent_default()`` is what
  keeps ``TextArea``'s own handler from also running.
* ``load_text`` posts ``Changed`` asynchronously, so the menu is recomputed from
  :meth:`ChatInput.edit`, which runs synchronously for every user edit, rather
  than from ``Changed``. Programmatic changes either go through ``load_text``
  (history recall, search preview, clearing) or run through :meth:`ChatInput._replace_quietly`.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from rich.cells import cell_len
from rich.console import RenderableType
from rich.segment import Segment
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Static, TextArea
from textual.widgets.text_area import Edit, EditResult, Location

from arcana_cli.tui.completion import SlashVocabulary, session_vocabulary, slash_completions
from arcana_cli.tui.history import AgentHistory, HistoryCursor
from arcana_cli.ui.input_model import (
    _PasteRegistry,
    _should_collapse_paste,
    _submits_on_enter,
)

#: Shown before the first line of the input.
PROMPT = "You › "

#: Marks each line after the first, right-aligned under the prompt's glyph.
CONTINUATION = "…"

#: Keys that insert a newline. Alt+Enter and Shift+Enter only arrive as such
#: from terminals speaking the kitty keyboard protocol. A legacy Alt+Enter is
#: ``ESC CR``, which Textual's parser reads as a plain ``enter`` (the alt is
#: dropped), so it submits: a known limitation (ADR-024 A4) rather than a parser
#: subclass on private API, tracked upstream in
#: https://github.com/Textualize/textual/issues/6378. Once a Textual release
#: fixes it, the known-limitation tests in tests/tui/test_chat_input.py fail:
#: flip them and raise the textual lower bound.
NEWLINE_KEYS = frozenset({"ctrl+j", "alt+enter", "shift+enter"})

#: The most completion rows shown at once; the menu scrolls to keep the highlight visible.
MENU_ROWS = 8


def _no_agents() -> Iterable[str]:
    return ()


def _keep_everything(_raw: str) -> bool:
    return True


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class CompletionMenu(Widget):
    """The slash-command completion list, shown under the input where the completed word starts."""

    COMPONENT_CLASSES = {"completion-menu--highlight"}

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.items: tuple[str, ...] = ()
        self.highlighted: int | None = None

    def show(self, items: tuple[str, ...], highlighted: int | None, column: int) -> None:
        """Show ``items`` with ``highlighted`` (an index, or none) starting at ``column``."""
        self.items = items
        self.highlighted = highlighted
        self.styles.margin = (0, 0, 0, column)
        self.add_class("-open")
        self.refresh(layout=True)

    def hide(self) -> None:
        self.items = ()
        self.highlighted = None
        self.remove_class("-open")

    @property
    def width_needed(self) -> int:
        """Columns the menu takes: its widest item plus a space either side."""
        return max((cell_len(item) for item in self.items), default=0) + 2

    def render(self) -> RenderableType:
        top = 0
        if self.highlighted is not None and self.highlighted >= MENU_ROWS:
            top = self.highlighted - MENU_ROWS + 1
        highlight = self.get_component_rich_style("completion-menu--highlight")
        width = self.width_needed
        rows: list[Text] = []
        for index, item in enumerate(self.items[top : top + MENU_ROWS], start=top):
            row = Text(f" {item} ", style=highlight if index == self.highlighted else "")
            row.align("left", width)
            rows.append(row)
        return Text("\n").join(rows)


class SearchBar(Static):
    """The reverse-i-search line: the query, and whether it matches."""

    def show(self, query: str, *, failing: bool) -> None:
        label = "failing reverse-i-search" if failing else "reverse-i-search"
        self.update(Text(f"({label})`{query}': "))
        self.add_class("-active")

    def hide(self) -> None:
        self.remove_class("-active")


@dataclass
class _Menu:
    """An open completion menu: its items, what it replaces, and the text to restore on Esc."""

    items: tuple[str, ...]
    start: Location
    word: str
    highlighted: int | None = None


@dataclass
class _Search:
    """A reverse-i-search in progress, and the text to restore if it's cancelled."""

    query: str
    match: int | None
    original: str
    original_location: Location


class ChatInput(TextArea):
    """The chat prompt box; see the module docstring for its keys.

    ``history`` is the agent's input history (in-memory if omitted); swap it
    with :meth:`set_history`. ``pastes`` holds the collapsed pastes of the
    message being typed. ``vocabulary`` is what slash completion completes
    against (the session-only commands if omitted), and ``agent_names`` supplies
    the agent names it offers where an agent is named. ``keep_in_history`` decides whether a submission is
    recorded in the history (a line carrying a secret isn't).
    """

    COMPONENT_CLASSES = TextArea.COMPONENT_CLASSES | {"chat-input--prompt", "chat-input--continuation"}

    BINDINGS = [Binding("ctrl+l", "repaint", "Repaint", show=False)]

    class Submitted(Message):
        """Enter submitted the input. ``text`` has pastes expanded; ``raw`` is as shown."""

        def __init__(self, chat_input: "ChatInput", text: str, raw: str) -> None:
            super().__init__()
            self.chat_input = chat_input
            self.text = text
            self.raw = raw

        @property
        def control(self) -> "ChatInput":
            return self.chat_input

    def __init__(
        self,
        *,
        history: AgentHistory | None = None,
        pastes: _PasteRegistry | None = None,
        agent_names: Callable[[], Iterable[str]] = _no_agents,
        vocabulary: SlashVocabulary | None = None,
        keep_in_history: Callable[[str], bool] = _keep_everything,
        prompt: str = PROMPT,
        id: str | None = None,
    ) -> None:
        # Set before TextArea's constructor, which already lays out the gutter.
        self.prompt = prompt
        super().__init__(
            soft_wrap=True,
            tab_behavior="focus",
            highlight_cursor_line=False,
            compact=True,
            id=id,
        )
        self.recall = history if history is not None else AgentHistory()
        self.pastes = pastes if pastes is not None else _PasteRegistry()
        self.agent_names = agent_names
        self.vocabulary = vocabulary if vocabulary is not None else session_vocabulary()
        self.keep_in_history = keep_in_history
        #: While set, Enter keeps the text instead of submitting it.
        self.busy = False
        self.menu = CompletionMenu()
        self.search_bar = SearchBar()
        self._cursor = HistoryCursor(self.recall)
        self._menu: _Menu | None = None
        self._search: _Search | None = None
        self._quieted = False

    # -- public state -----------------------------------------------------
    @property
    def menu_open(self) -> bool:
        return self._menu is not None

    @property
    def searching(self) -> bool:
        return self._search is not None

    def set_history(self, history: AgentHistory) -> None:
        """Scope Up/Down, the suggestion and Ctrl+R to ``history`` from now on."""
        self.recall = history
        self._cursor = HistoryCursor(history)
        self.update_suggestion()

    def action_repaint(self) -> None:
        """Ctrl+L: redraw the whole screen."""
        self.screen.refresh(repaint=True, layout=True)

    # -- gutter: the prompt on the first line, a continuation mark after ---
    @property
    def gutter_width(self) -> int:
        return cell_len(self.prompt)

    def render_line(self, y: int) -> Strip:
        line = super().render_line(y)
        width = self.gutter_width
        row = self.scroll_offset.y + y
        if row == 0:
            label, component = self.prompt, "chat-input--prompt"
        elif row < self.wrapped_document.height:
            label, component = f"{CONTINUATION:>{max(width - 1, 1)}} ", "chat-input--continuation"
        else:
            label, component = " " * width, "chat-input--continuation"
        style = self.rich_style + self.get_component_rich_style(component)
        return Strip.join([Strip([Segment(label, style)], width), line])

    # -- edits ------------------------------------------------------------
    def edit(self, edit: Edit) -> EditResult:
        """Apply ``edit``; a user edit recomputes the completion menu from the new text."""
        result = super().edit(edit)
        if not self._quieted:
            self._refresh_menu()
        return result

    def undo(self) -> None:
        super().undo()
        self._refresh_menu()

    def redo(self) -> None:
        super().redo()
        self._refresh_menu()

    def update_suggestion(self) -> None:
        """The ghost text: the rest of a history line, shown only at the end of the text."""
        if self._search is not None or self._menu is not None or not self.cursor_at_end_of_text:
            self.suggestion = ""
        else:
            self.suggestion = self.recall.suggest(self.text)

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        # Judged on the cursor as it is now, not the event's selection: under fast
        # typing an earlier key's event arrives after later keys moved the cursor on.
        # A cursor moved away from the completion closes the menu.
        if self._menu is not None and not self._quieted and self.cursor_location != self._menu_end():
            self._close_menu()
        self.update_suggestion()

    # -- keys -------------------------------------------------------------
    async def _on_key(self, event: events.Key) -> None:
        if self.read_only:
            return
        handled = self._search_key(event) if self._search is not None else self._edit_key(event)
        if handled:
            event.stop()
            event.prevent_default()

    async def _on_paste(self, event: events.Paste) -> None:
        if self.read_only:
            return
        event.stop()
        event.prevent_default()
        if self._search is not None:
            self._accept_search()
        data = _normalize_newlines(event.text)
        text = self.pastes.collapse(data) if _should_collapse_paste(data) else data
        start, end = self.selection
        self.replace(text, start, end, maintain_selection_offset=False)

    def _edit_key(self, event: events.Key) -> bool:
        key = event.key
        if key == "ctrl+r":
            self._start_search()
            return True
        if self._menu is not None:
            if key in ("tab", "down"):
                self._step_menu(1)
                return True
            if key in ("shift+tab", "up"):
                self._step_menu(-1)
                return True
            if key == "escape":
                self._cancel_menu()
                return True
        if key == "tab":
            self._open_menu()
            return True
        if key == "shift+tab":
            return True
        if key == "enter":
            self._enter()
            return True
        if key in NEWLINE_KEYS:
            start, end = self.selection
            self.replace("\n", start, end, maintain_selection_offset=False)
            return True
        if key in ("up", "down") and self.selection.is_empty:
            return self._recall(older=key == "up")
        return False

    def _enter(self) -> None:
        row, column = self.cursor_location
        if not _submits_on_enter(self._text_before_cursor()):
            # Drop the trailing "\" continuation marker and break the line.
            self.replace("\n", (row, column - 1), (row, column), maintain_selection_offset=False)
            return
        self._close_menu()
        if self.busy:
            return
        raw = self.text
        text = self.pastes.expand(raw)
        if self.keep_in_history(raw):
            self.recall.append(raw)
        self.pastes.clear()
        self._cursor.reset()
        self.load_text("")
        self.post_message(self.Submitted(self, text, raw))

    def _text_before_cursor(self) -> str:
        return self.get_text_range((0, 0), self.cursor_location)

    # -- history recall ---------------------------------------------------
    def _recall(self, *, older: bool) -> bool:
        """Up on the first row / Down on the last walk history; elsewhere the cursor moves."""
        row = self.wrapped_document.location_to_offset(self.cursor_location).y
        if older and row != 0:
            return False
        if not older and row != self.wrapped_document.height - 1:
            return False
        text = self._cursor.older(self.text) if older else self._cursor.newer(self.text)
        if text is None:
            return True
        self._close_menu()
        self.load_text(text)
        # Like prompt_toolkit: back lands at the end of the text, forward at the end of its first line.
        self.move_cursor(self.document.end if older else (0, len(self.document[0])))
        return True

    # -- completion menu --------------------------------------------------
    def _replace_quietly(self, text: str, start: Location, end: Location) -> None:
        """Replace ``start``–``end`` with ``text`` without recomputing the completion menu."""
        self._quieted = True
        try:
            self.replace(text, start, end, maintain_selection_offset=False)
        finally:
            self._quieted = False

    def _menu_end(self) -> Location:
        """Where the cursor sits after the menu's current item (or the typed word)."""
        assert self._menu is not None
        menu = self._menu
        shown = menu.word if menu.highlighted is None else menu.items[menu.highlighted]
        return (menu.start[0], menu.start[1] + len(shown))

    def _refresh_menu(self) -> None:
        """Recompute the menu from the text before the cursor, opening or closing it."""
        before = self._text_before_cursor()
        found = slash_completions(before, self.vocabulary, self.agent_names)
        word = before[found.start :]
        # A lone candidate that adds nothing isn't worth a menu.
        if not found.items or found.items == (word,):
            self._close_menu()
            return
        lines = before[: found.start].split("\n")
        self._menu = _Menu(items=found.items, start=(len(lines) - 1, len(lines[-1])), word=word)
        self._show_menu()

    def _open_menu(self) -> None:
        if self._search is None:
            self._refresh_menu()

    def _show_menu(self) -> None:
        assert self._menu is not None
        self.suggestion = ""
        # Anchored where the completed word starts (as prompt_toolkit's menu is), so
        # cycling doesn't move it; the item's leading pad sits one column left of it.
        start = self.wrapped_document.location_to_offset(self._menu.start)
        column = start.x + self.gutter_width - self.scroll_offset.x - 1
        column = max(0, min(column, self.size.width - self.menu.width_needed))
        self.menu.show(self._menu.items, self._menu.highlighted, column)

    def _step_menu(self, step: int) -> None:
        """Move the highlight by ``step``; past either end is the typed word, as prompt_toolkit does."""
        assert self._menu is not None
        menu = self._menu
        count = len(menu.items)
        # Positions cycle through: typed word (-1), item 0 … item count-1.
        position = -1 if menu.highlighted is None else menu.highlighted
        position = (position + 1 + step) % (count + 1) - 1
        end = self._menu_end()
        menu.highlighted = None if position < 0 else position
        shown = menu.word if menu.highlighted is None else menu.items[menu.highlighted]
        self._replace_quietly(shown, menu.start, end)
        self._show_menu()

    def _cancel_menu(self) -> None:
        """Esc: put back the typed word and close the menu."""
        assert self._menu is not None
        menu = self._menu
        if menu.highlighted is not None:
            self._replace_quietly(menu.word, menu.start, self._menu_end())
        self._close_menu()

    def _close_menu(self) -> None:
        if self._menu is None:
            return
        self._menu = None
        self.menu.hide()
        self.update_suggestion()

    # -- reverse-i-search -------------------------------------------------
    def _start_search(self) -> None:
        self._close_menu()
        self._search = _Search(query="", match=None, original=self.text, original_location=self.cursor_location)
        self.suggestion = ""
        self.search_bar.show("", failing=False)

    def _search_key(self, event: events.Key) -> bool:
        assert self._search is not None
        search = self._search
        key = event.key
        if key == "ctrl+r":
            self._find(search.query, before=search.match)
            return True
        if key == "enter":
            self._accept_search()
            return True
        if key in ("escape", "ctrl+g"):
            self._cancel_search()
            return True
        if key == "backspace":
            search.query = search.query[:-1]
            self._find(search.query, before=None)
            return True
        if event.is_printable and event.character is not None:
            search.query += event.character
            # Narrowing keeps the current match if it still contains the query.
            self._find(search.query, before=None if search.match is None else search.match + 1)
            return True
        # Any other key takes the match and then does its usual job.
        self._accept_search()
        return self._edit_key(event)

    def _find(self, query: str, *, before: int | None) -> None:
        assert self._search is not None
        search = self._search
        found = self.recall.search(query, before=before)
        if found is not None:
            search.match = found
            self.load_text(self.recall.entries[found])
            self.move_cursor(self.document.end)
        self.search_bar.show(query, failing=bool(query) and found is None)

    def _accept_search(self) -> None:
        self._search = None
        self.search_bar.hide()
        self._cursor.reset()
        self.update_suggestion()

    def _cancel_search(self) -> None:
        assert self._search is not None
        search = self._search
        self._search = None
        self.search_bar.hide()
        if self.text != search.original:
            self.load_text(search.original)
        self.move_cursor(search.original_location)
        self.update_suggestion()


class ChatInputPanel(Vertical):
    """A :class:`ChatInput` with its completion menu and search bar stacked underneath.

    ``chat_input`` defaults to a fresh input with the id ``chat-input``.
    """

    def __init__(self, chat_input: ChatInput | None = None, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.input = chat_input if chat_input is not None else ChatInput(id="chat-input")

    def compose(self) -> ComposeResult:
        yield self.input
        yield self.input.menu
        yield self.input.search_bar
