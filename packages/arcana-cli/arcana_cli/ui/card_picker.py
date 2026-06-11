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

from arcana.cards.registry import CardRegistry, get_registry
from arcana.types.card import Card, TarotCard
from arcana_cli.constants import ROMAN


def _render_preview(card: TarotCard, registry: CardRegistry) -> Panel:
    a = card.archetype
    pi = a.prompt_ingredients
    mw = a.memory_weights
    dc = a.decay_config

    def _card_names(card_list: list[Card]) -> str:
        return ", ".join(registry.get(c).name for c in card_list) if card_list else "—"

    def _half_life(days: float | None) -> str:
        return f"{days}d" if days is not None else "system default"

    lines = [
        f"[bold]{ROMAN[card.number]} · {card.name}[/bold]  [dim]{card.id.value}[/dim]",
        f"[cyan]Role:[/cyan]        {a.role}",
        f"[cyan]Temperature:[/cyan] {a.default_temperature:.2f}",
        f"[cyan]Core traits:[/cyan] {', '.join(a.core_traits)}",
        "",
        "[bold]Prompt Ingredients[/bold]",
        f"  [cyan]Tone:[/cyan]       {pi.tone}",
        f"  [cyan]Approach:[/cyan]   {pi.approach}",
        "  [cyan]Priorities:[/cyan]",
    ]
    for p in pi.priorities:
        lines.append(f"    • {p}")
    lines.append("  [cyan]Avoid:[/cyan]")
    for av in pi.avoid:
        lines.append(f"    • {av}")
    lines += [
        "",
        "[bold]Memory Weights[/bold]",
        f"  episodic {mw.episodic:.2f}   semantic {mw.semantic:.2f}"
        f"   procedural {mw.procedural:.2f}   preference {mw.preference:.2f}",
        "",
        "[bold]Decay Half-lives[/bold]",
        f"  episodic {_half_life(dc.episodic_half_life_days)}"
        f"   semantic {_half_life(dc.semantic_half_life_days)}"
        f"   procedural {_half_life(dc.procedural_half_life_days)}"
        f"   preference {_half_life(dc.preference_half_life_days)}",
        "",
        f"[cyan]Synergies:[/cyan]   {_card_names(card.synergy_cards)}",
        f"[cyan]Tensions:[/cyan]    {_card_names(card.tension_cards)}",
    ]
    if a.preferred_tool_categories:
        lines.append(f"[cyan]Tools:[/cyan]       {', '.join(a.preferred_tool_categories)}")
    lines += [
        "",
        f"[cyan]Reversed:[/cyan]    {card.reversed_meaning}",
        f"[cyan]Trigger:[/cyan]     {card.reversed_trigger}",
        "",
        f"[dim]{card.imagery}[/dim]",
    ]
    return Panel("\n".join(lines), title=f"🃏 {card.name}", border_style="magenta")


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
        raw = input(f"\n{prompt} (comma-separated names or keys, blank to cancel): ").strip()
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
        raw = input(f"\n{prompt} (name or key, blank to cancel): ").strip()
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


def _run_picker(prompt: str, multi: bool) -> list[Card]:
    if not sys.stdin.isatty():
        return _non_tty_fallback(prompt, multi)

    registry = get_registry()
    all_cards = registry.all()

    cursor = 0
    selected: set[Card] = set()
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
            _render_preview(preview_card, registry)
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


def select_card(prompt: str = "Select a card") -> Card | None:
    """Single-card picker. Returns the chosen Card, or None if cancelled."""
    result = _run_picker(prompt, multi=False)
    return result[0] if result else None


def select_cards(prompt: str = "Select cards") -> list[Card]:
    """Multi-card picker. Space toggles, Enter confirms. Returns empty list if cancelled."""
    return _run_picker(prompt, multi=True)
