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


def _render_list(
    cards: list[TarotCard],
    cursor: int,
    selected: set[Card],
    filter_buf: str,
    filtering: bool,
    multi: bool,
) -> Panel:
    rows: list[Text] = []
    for i, card in enumerate(cards):
        is_cursor = i == cursor
        is_selected = card.id in selected

        check = "✓ " if is_selected else "  "
        arrow = "▶" if is_cursor else " "
        roman = ROMAN[card.number]
        line = f" {arrow} {check}{roman}. {card.name}"

        t = Text(line)
        if is_cursor and is_selected:
            t.stylize("bold reverse green")
        elif is_cursor:
            t.stylize("bold reverse magenta")
        elif is_selected:
            t.stylize("bold green")
        rows.append(t)

    if filtering:
        hint = Text(f" / {filter_buf}▌", style="bold yellow")
    elif filter_buf:
        hint = Text(f" / {filter_buf}  [Esc clear]", style="dim")
    elif multi:
        hint = Text(" [↑↓] nav  [Space] toggle  [Enter] confirm  [/] filter  [Esc] cancel", style="dim")
    else:
        hint = Text(" [↑↓] nav  [Enter] select  [/] filter  [Esc] cancel", style="dim")

    title = "🃏 Major Arcana"
    if multi and selected:
        title += f" ({len(selected)} selected)"

    content = Group(*rows, Text(""), hint) if rows else Group(Text("  (no matches)"), Text(""), hint)
    return Panel(content, title=title, border_style="blue")


def _non_tty_fallback(prompt: str, multi: bool) -> list[Card]:
    """Plain typed-prompt fallback when stdin is not a TTY."""
    registry = get_registry()
    console = Console()

    console.print("[bold]Available cards:[/bold]")
    for card in registry.all():
        console.print(f"  {ROMAN[card.number]}. {card.name}  [dim]{card.id.value}[/dim]")

    if multi:
        console.print(f"\n[dim]{prompt}[/dim] (comma-separated names or keys, blank for none): ", end="")
        raw = sys.stdin.readline().strip()
        if not raw:
            return []
        result: list[Card] = []
        for part in raw.split(","):
            part = part.strip().lower()
            matches = [c for c in registry.all() if part in c.name.lower() or part in c.id.value]
            if len(matches) == 1:
                result.append(matches[0].id)
            elif len(matches) > 1:
                console.print(f"[yellow]Ambiguous '{part}', skipping[/yellow]")
            else:
                console.print(f"[red]Unknown card '{part}', skipping[/red]")
        return result
    else:
        console.print(f"\n[dim]{prompt}[/dim] (name or key, blank to cancel): ", end="")
        raw = sys.stdin.readline().strip()
        if not raw:
            return []
        part = raw.lower()
        matches = [c for c in registry.all() if part in c.name.lower() or part in c.id.value]
        if len(matches) == 1:
            return [matches[0].id]
        if len(matches) > 1:
            console.print(f"[yellow]Ambiguous: {', '.join(c.name for c in matches)}[/yellow]")
        else:
            console.print(f"[red]Unknown card: {raw!r}[/red]")
        return []


def _run_picker(
    prompt: str,
    multi: bool,
    initial_card: Card | None = None,
    initial_selected: list[Card] | None = None,
) -> list[Card]:
    if not sys.stdin.isatty():
        return _non_tty_fallback(prompt, multi)

    registry = get_registry()
    all_cards = registry.all()

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
        layout["list"].update(_render_list(cards, cursor, selected, filter_buf, filtering, multi))
        preview_card = cards[cursor] if cards else None
        layout["preview"].update(
            card_panel(preview_card, registry)
            if preview_card
            else Panel("[dim]No cards match.[/dim]", border_style="dim")
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
                    else:
                        selected.add(card_id)
            elif key == readchar.key.ESC:
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
) -> list[Card]:
    """Multi-card picker. Space toggles, Enter confirms. Returns empty list if cancelled.

    Pass `initial` to pre-select cards and position the cursor on the first one.
    """
    return _run_picker(prompt, multi=True, initial_selected=initial or [])
