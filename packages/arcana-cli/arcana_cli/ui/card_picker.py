"""Interactive card picker — rich.Live two-pane layout with readchar input.

Public API:
    select_card()   → Card | None        (single selection)
    select_cards()  → list[Card]         (multi-select with Space)

Falls back to a plain typed prompt when stdin is not a TTY.
"""

import sys

import readchar
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from arcana.cards.registry import get_registry
from arcana.types.card import Card, TarotCard
from arcana_cli.constants import ROMAN
from arcana_cli.ui.card_panel import card_panel
from arcana_cli.ui.theme import (
    PICKER_BORDER,
    PICKER_BORDER_PREVIEW,
    PICKER_CURSOR,
    PICKER_CURSOR_SELECTED,
    PICKER_HINT_DIM,
    PICKER_HINT_FILTER,
    PICKER_SELECTED,
    TXT3,
    err,
    eyebrow,
    warn,
)


def _render_list(
    cards: list[TarotCard],
    cursor: int,
    selected: set[Card],
    filter_buf: str,
    filtering: bool,
    multi: bool,
    max_items: int | None = None,
) -> Panel:
    at_limit = max_items is not None and len(selected) >= max_items

    rows: list[Text] = []
    for i, card in enumerate(cards):
        is_cursor = i == cursor
        is_selected = card.id in selected
        is_blocked = at_limit and not is_selected

        check = "✓ " if is_selected else "  "
        arrow = "▶" if is_cursor else " "
        roman = ROMAN[card.number]
        line = f" {arrow} {check}{roman}. {card.name}"

        t = Text(line)
        if is_cursor and is_selected:
            t.stylize(PICKER_CURSOR_SELECTED)
        elif is_cursor:
            t.stylize(PICKER_CURSOR)
        elif is_selected:
            t.stylize(PICKER_SELECTED)
        elif is_blocked:
            t.stylize(PICKER_HINT_DIM)
        rows.append(t)

    if filtering:
        hint = Text(f" / {filter_buf}▌", style=PICKER_HINT_FILTER)
    elif filter_buf:
        hint = Text(f" / {filter_buf}  [Esc clear]", style=PICKER_HINT_DIM)
    elif multi and at_limit:
        hint = Text(
            f" limit reached ({max_items})  [Space] deselect  [Enter] confirm  [Esc] cancel",
            style=PICKER_HINT_DIM,
        )
    elif multi:
        limit_note = f"  max {max_items}" if max_items is not None else ""
        hint = Text(
            f" [↑↓] nav  [Space] toggle{limit_note}  [Enter] confirm  [/] filter  [Esc] cancel",
            style=PICKER_HINT_DIM,
        )
    else:
        hint = Text(" [↑↓] nav  [Enter] select  [/] filter  [Esc] cancel", style=PICKER_HINT_DIM)

    title = "Major Arcana"
    if multi and selected:
        title += f" ({len(selected)}"
        title += f"/{max_items}" if max_items is not None else ""
        title += " selected)"

    content = Group(*rows, Text(""), hint) if rows else Group(Text("  (no matches)"), Text(""), hint)
    return Panel(content, title=title, border_style=PICKER_BORDER)


def _non_tty_fallback(prompt: str, multi: bool, exclude: set[Card] | None = None) -> list[Card]:
    """Numbered list + typed-prompt fallback when stdin is not a TTY."""
    registry = get_registry()
    console = Console()
    all_cards = [c for c in registry.all() if not exclude or c.id not in exclude]

    console.print(eyebrow("Available cards"))
    for i, card in enumerate(all_cards, 1):
        console.print(f"  {i:2}. {card.name:<24}  [{TXT3}]{card.id.value}[/]")

    def _resolve_one(raw: str) -> Card | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(all_cards):
                return all_cards[idx].id
            console.print(err(f"Number out of range: {raw!r}"))
            return None
        except ValueError:
            pass
        q = raw.lower()
        matches = [c for c in all_cards if q in c.name.lower() or q in c.id.value]
        if len(matches) == 1:
            return matches[0].id
        if len(matches) > 1:
            console.print(warn(f"Ambiguous '{raw}', skipping"))
        else:
            console.print(err(f"Unknown card '{raw}'"))
        return None

    if multi:
        console.print(f"\n[{TXT3}]{prompt}[/] (comma-separated #s or names, blank for none): ", end="")
        raw = sys.stdin.readline().strip()
        if not raw:
            return []
        return [c for part in raw.split(",") if (c := _resolve_one(part)) is not None]
    else:
        console.print(f"\n[{TXT3}]{prompt}[/] (# or name/key, blank to cancel): ", end="")
        raw = sys.stdin.readline().strip()
        if not raw:
            return []
        card_id = _resolve_one(raw)
        return [card_id] if card_id is not None else []


def _run_picker(
    prompt: str,
    multi: bool,
    initial_card: Card | None = None,
    initial_selected: list[Card] | None = None,
    max_items: int | None = None,
    exclude: set[Card] | None = None,
) -> list[Card]:
    if not sys.stdin.isatty():
        return _non_tty_fallback(prompt, multi, exclude)

    registry = get_registry()
    all_cards = [c for c in registry.all() if not exclude or c.id not in exclude]

    # Pre-position cursor on initial_card (or first of initial_selected)
    seed = initial_card or (initial_selected[0] if initial_selected else None)
    cursor = next((i for i, c in enumerate(all_cards) if c.id == seed), 0)

    selected: set[Card] = set(initial_selected or [])
    filter_buf = ""
    filtering = False

    console = Console()

    def visible() -> list[TarotCard]:
        if not filter_buf:
            return all_cards
        q = filter_buf.lower()
        return [c for c in all_cards if q in c.name.lower() or q in c.id.value]

    def make_layout(cards: list[TarotCard]) -> Layout:
        layout = Layout()
        layout.split_row(
            Layout(name="list", ratio=1),
            Layout(name="preview", ratio=2),
        )
        layout["list"].update(_render_list(cards, cursor, selected, filter_buf, filtering, multi, max_items))
        preview_card = cards[cursor] if cards else None
        layout["preview"].update(
            card_panel(preview_card, registry)
            if preview_card
            else Panel(f"[{TXT3}]No cards match.[/]", border_style=PICKER_BORDER_PREVIEW)
        )
        return layout

    result: list[Card] = []

    with Live(make_layout(visible()), console=console, screen=True, refresh_per_second=30) as live:
        while True:
            cards = visible()
            if cards:
                cursor = min(cursor, len(cards) - 1)
            live.update(make_layout(cards))

            key = readchar.readkey()

            if filtering:
                if key == readchar.key.ENTER:
                    filtering = False
                elif key == readchar.key.ESC:
                    filtering = False
                    filter_buf = ""
                    cursor = 0
                elif key == readchar.key.BACKSPACE:
                    filter_buf = filter_buf[:-1]
                    cursor = 0
                elif len(key) == 1 and key.isprintable():
                    filter_buf += key
                    cursor = 0
            elif key == readchar.key.UP:
                if cards:
                    cursor = max(0, cursor - 1)
            elif key == readchar.key.DOWN:
                if cards:
                    cursor = min(len(cards) - 1, cursor + 1)
            elif key == "/":
                filtering = True
                filter_buf = ""
                cursor = 0
            elif key == readchar.key.ENTER:
                if cards:
                    result = list(selected) if multi else [cards[cursor].id]
                break
            elif key == readchar.key.SPACE and multi:
                if cards:
                    card_id = cards[cursor].id
                    if card_id in selected:
                        selected.discard(card_id)
                    elif max_items is None or len(selected) < max_items:
                        selected.add(card_id)
            elif key in (readchar.key.ESC, readchar.key.CTRL_C):
                result = []
                break

    return result


def select_card(
    prompt: str = "Select a card",
    *,
    initial: Card | None = None,
) -> Card | None:
    """Single-card picker. Returns the chosen Card, or None if cancelled.

    Pass `initial` to pre-position the cursor on a specific card.
    """
    result = _run_picker(prompt, multi=False, initial_card=initial)
    return result[0] if result else None


def select_cards(
    prompt: str = "Select cards",
    *,
    initial: list[Card] | None = None,
    max_items: int | None = None,
    exclude: set[Card] | None = None,
) -> list[Card]:
    """Multi-card picker. Space toggles, Enter confirms. Returns empty list if cancelled.

    Pass `initial` to pre-select cards and position the cursor on the first one.
    Pass `max_items` to cap how many cards can be selected simultaneously.
    Pass `exclude` to hide specific cards from the picker (e.g. the primary card, Card.WORLD).
    """
    return _run_picker(prompt, multi=True, initial_selected=initial or [], max_items=max_items, exclude=exclude)
