"""The two-pane picker: a filterable list beside a live preview of the highlighted choice.

:class:`CardPickerScreen` is the picker for choices that carry a preview (cards,
with their :func:`~arcana_cli.ui.card_panel.card_panel`; agents, with their
primary card's). Inside the session a
:class:`~arcana_cli.ui.renderer.textual_renderer.TextualRenderer` pushes it over
the chat; from a one-shot command :func:`pick` runs it in :class:`PickerApp`, a
short-lived app on the invocation's own event loop. Either way it is the same
screen with the same keys:

* typing filters the list (Backspace widens it again); the filter matches a
  choice's label, or its value when the value is a string (a card's key),
  ignoring case and spaces;
* Up / Down move the cursor and the preview follows it;
* Enter picks the highlighted choice, or confirms a multi-select;
* Space toggles a choice in a multi-select, up to ``max_items`` (a Space past the
  cap is refused with a hint, never silently); in a single pick it is typed
  into the filter;
* Esc or Ctrl+C cancels.

It dismisses with the picked indexes into ``choices`` (in list order), or
``None`` when cancelled. Disabled choices are left out.
"""

from collections.abc import Sequence
from typing import Any

from rich.console import RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from arcana_cli.tui.app import ArcanaBaseApp
from arcana_cli.ui.renderer.port import Choice
from arcana_cli.ui.theme import (
    CHECK,
    PICKER_BORDER_PREVIEW,
    PICKER_CURSOR,
    PICKER_CURSOR_SELECTED,
    PICKER_HINT_DIM,
    PICKER_HINT_FILTER,
    PICKER_SELECTED,
    TXT3,
)

#: Shown in the preview pane when the filter matches nothing.
NO_MATCHES = "No matches."


def _squash(text: str) -> str:
    """``text`` lower-cased with its whitespace removed: the form the filter compares."""
    return "".join(text.lower().split())


def _matches(choice: Choice[Any], query: str) -> bool:
    """Whether ``choice`` survives the filter ``query`` (already squashed).

    Whitespace is ignored on both sides, so ``highpri`` finds "The High
    Priestess" and a multi-select, where Space toggles, never needs a space
    typed into the filter.
    """
    if query in _squash(choice.label):
        return True
    return isinstance(choice.value, str) and query in _squash(choice.value)


class FilterInput(Input):
    """The picker's filter box; in a multi-select it lets Space through to the screen's toggle.

    An ``Input`` claims every printable key (``check_consume_key``), which
    takes the key away from the screen's bindings; declining Space keeps the
    toggle binding live. The filter ignores spaces, so none is ever needed.
    """

    def __init__(self, *, spaces: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spaces = spaces

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if key == "space" and not self._spaces:
            return False
        return super().check_consume_key(key, character)


class CardPickerScreen(ModalScreen[list[int] | None]):
    """A filterable list of ``choices`` beside a preview of the highlighted one.

    ``initial`` holds indexes into ``choices``: the cursor starts on the first,
    and a multi-select starts with all of them picked.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "submit", "Pick", show=False, priority=True),
        Binding("up", "move(-1)", "Up", show=False, priority=True),
        Binding("down", "move(1)", "Down", show=False, priority=True),
        Binding("space", "toggle_pick", "Toggle", show=False, priority=True),
    ]

    def __init__(
        self,
        choices: Sequence[Choice[Any]],
        *,
        multi: bool = False,
        initial: Sequence[int] = (),
        title: str = "",
        max_items: int | None = None,
    ) -> None:
        super().__init__()
        self._choices = choices
        self._multi = multi
        self._title = title or ("Select" if multi else "Select one")
        self._max_items = max_items
        self._offered = [i for i, c in enumerate(choices) if not c.disabled]
        seeds = [i for i in initial if i in self._offered]
        self._selected: set[int] = set(seeds) if multi else set()
        self._visible = list(self._offered)
        self._cursor_seed = seeds[0] if seeds else None
        self._notice = ""
        # Held directly rather than queried: the screen drives all three on every key.
        # "/" is refused: no label needs it, and pressing it first (how filtering
        # used to start) then filters as expected instead of matching nothing.
        self.filter = FilterInput(
            spaces=not multi, restrict=r"[^/]*", placeholder="type to filter", id="picker-filter", compact=True
        )
        self.options = OptionList(id="picker-options")
        # Focus stays on the filter so typing always filters; the list is driven by the screen's keys.
        self.options.can_focus = False
        self.preview = Static(id="picker-preview")

    # -- layout -------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-picker"):
            with Vertical(classes="card-picker-list"):
                yield Static("", classes="dialog-title", id="picker-title")
                yield self.filter
                yield self.options
                yield Static("", classes="dialog-hint", id="picker-hint")
            with VerticalScroll(classes="card-picker-preview"):
                yield self.preview

    def on_mount(self) -> None:
        self._refill(keep=self._cursor_seed)
        self.filter.focus()

    # -- state --------------------------------------------------------------
    @property
    def highlighted(self) -> int | None:
        """The index into ``choices`` under the cursor, or ``None`` when nothing matches."""
        row = self.options.highlighted
        return self._visible[row] if row is not None and row < len(self._visible) else None

    @property
    def listed(self) -> list[int]:
        """The indexes into ``choices`` the filter currently lists, in list order."""
        return list(self._visible)

    @property
    def selected(self) -> list[int]:
        """The picked indexes into ``choices`` so far, in list order (a multi-select's running pick)."""
        return sorted(self._selected)

    def _at_limit(self) -> bool:
        return self._max_items is not None and len(self._selected) >= self._max_items

    def _row(self, index: int, *, cursor: bool) -> Text:
        """One list row: cursor arrow, check mark (multi), and the label, styled by state."""
        picked = index in self._selected
        check = f"{CHECK} " if picked else "  "
        arrow = "▶" if cursor else " "
        row = Text(f"{arrow} {check if self._multi else ''}{self._choices[index].label}")
        if cursor and picked:
            row.stylize(PICKER_CURSOR_SELECTED)
        elif cursor:
            row.stylize(PICKER_CURSOR)
        elif picked:
            row.stylize(PICKER_SELECTED)
        elif self._multi and self._at_limit():
            row.stylize(PICKER_HINT_DIM)
        return row

    def _restyle(self) -> None:
        """Redraw every visible row, the title and the hint for the current cursor and picks."""
        cursor = self.options.highlighted
        for row, index in enumerate(self._visible):
            self.options.replace_option_prompt_at_index(row, self._row(index, cursor=row == cursor))
        title = self._title
        if self._multi and self._selected:
            cap = f"/{self._max_items}" if self._max_items is not None else ""
            title += f" ({len(self._selected)}{cap} selected)"
        self.query_one("#picker-title", Static).update(escape(title))
        self.query_one("#picker-hint", Static).update(self._hint())

    def _hint(self) -> Text:
        if self._notice:
            return Text(self._notice, style=PICKER_HINT_FILTER)
        if self._multi:
            cap = f"  max {self._max_items}" if self._max_items is not None else ""
            return Text(f"↑↓ move · Space toggle{cap} · Enter confirm · Esc cancel", style=TXT3)
        return Text("↑↓ move · Enter pick · Esc cancel", style=TXT3)

    def _refill(self, *, keep: int | None = None) -> None:
        """Rebuild the list for the current filter; the cursor goes to ``keep`` if visible, else the top."""
        query = _squash(self.filter.value)
        self._visible = [i for i in self._offered if not query or _matches(self._choices[i], query)]
        self.options.set_options(Text(self._choices[i].label) for i in self._visible)
        if self._visible:
            self.options.highlighted = self._visible.index(keep) if keep in self._visible else 0
        self._restyle()
        self._show_preview()

    def _show_preview(self) -> None:
        index = self.highlighted
        preview: RenderableType
        if index is None:
            preview = Panel(Text(NO_MATCHES, style=TXT3), border_style=PICKER_BORDER_PREVIEW)
        else:
            choice = self._choices[index]
            preview = (
                choice.preview
                if choice.preview is not None
                else Panel(Text(choice.label), border_style=PICKER_BORDER_PREVIEW)
            )
        self.preview.update(preview)

    # -- events -------------------------------------------------------------
    @on(Input.Changed, "#picker-filter")
    def _filtered(self) -> None:
        self._notice = ""
        self._refill()

    @on(OptionList.OptionHighlighted, "#picker-options")
    def _highlighted(self) -> None:
        self._restyle()
        self._show_preview()

    @on(OptionList.OptionSelected, "#picker-options")
    def _clicked(self) -> None:
        """A click picks (single) or toggles (multi) the clicked row; focus stays on the filter."""
        self.filter.focus()
        if self._multi:
            self.action_toggle_pick()
        else:
            self.action_submit()

    # -- actions ------------------------------------------------------------
    def action_move(self, step: int) -> None:
        if not self._visible:
            return
        row = self.options.highlighted if self.options.highlighted is not None else 0
        self.options.highlighted = max(0, min(len(self._visible) - 1, row + step))

    def action_toggle_pick(self) -> None:
        index = self.highlighted
        if not self._multi or index is None:
            return
        if index in self._selected:
            self._selected.discard(index)
            self._notice = ""
        elif self._at_limit():
            self._notice = f"Limit reached ({self._max_items}): deselect one first."
        else:
            self._selected.add(index)
            self._notice = ""
        self._restyle()

    def action_submit(self) -> None:
        if self._multi:
            self.dismiss(self.selected)
        elif (index := self.highlighted) is not None:
            self.dismiss([index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class PickerApp(ArcanaBaseApp[list[int] | None]):
    """A short-lived app that shows one :class:`CardPickerScreen` and exits with its answer."""

    def __init__(self, screen: CardPickerScreen) -> None:
        super().__init__()
        self._picker = screen

    def on_mount(self) -> None:
        self.push_screen(self._picker, callback=self.exit)


async def pick(
    choices: Sequence[Choice[Any]],
    *,
    multi: bool = False,
    initial: Sequence[int] = (),
    title: str = "",
    max_items: int | None = None,
) -> list[int]:
    """Run the picker over ``choices`` on the current loop; the picked indexes, ``[]`` if cancelled."""
    app = PickerApp(CardPickerScreen(choices, multi=multi, initial=initial, title=title, max_items=max_items))
    picked = await app.run_here()
    return picked if picked is not None else []
