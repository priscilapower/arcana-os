"""arcana cards — list and show tarot card definitions."""

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from arcana.cards.registry import get_registry
from arcana.types.card import Card, TarotCard

app = typer.Typer(help="Browse the 22 Major Arcana card definitions.")
console = Console()

_ROMAN = {
    0: "0",
    1: "I",
    2: "II",
    3: "III",
    4: "IV",
    5: "V",
    6: "VI",
    7: "VII",
    8: "VIII",
    9: "IX",
    10: "X",
    11: "XI",
    12: "XII",
    13: "XIII",
    14: "XIV",
    15: "XV",
    16: "XVI",
    17: "XVII",
    18: "XVIII",
    19: "XIX",
    20: "XX",
    21: "XXI",
}


def _resolve_card(name: str) -> TarotCard:
    registry = get_registry()
    for candidate in (name, f"the-{name}"):
        try:
            return registry.get(Card(candidate))
        except ValueError:
            pass
    matches = [c for c in registry.all() if name.lower() in c.id.value or name.lower() in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        console.print(f"[yellow]Ambiguous: {', '.join(c.name for c in matches)}[/yellow]")
        raise typer.Exit(1)
    console.print(f"[red]Unknown card: {name!r}[/red]")
    raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def list_cards(ctx: typer.Context) -> None:
    """List all 22 Major Arcana with archetype and temperature."""
    if ctx.invoked_subcommand is not None:
        return
    registry = get_registry()
    table = Table(title="🃏 The 22 Major Arcana", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=5)
    table.add_column("Card", style="bold", min_width=20)
    table.add_column("Role")
    table.add_column("Temp", width=6, justify="right")
    table.add_column("Key", style="dim")
    for card in registry.all():
        table.add_row(
            _ROMAN[card.number],
            card.name,
            card.archetype.role,
            f"{card.archetype.default_temperature:.2f}",
            card.id.value,
        )
    console.print(table)


@app.command("show")
def show(name: str = typer.Argument(..., help="Card name or key (e.g. 'hermit', 'the-hermit')")) -> None:
    """Show full card details — prompt ingredients, memory weights, synergies."""
    card = _resolve_card(name)
    a = card.archetype
    pi = a.prompt_ingredients
    mw = a.memory_weights
    dc = a.decay_config
    registry = get_registry()

    def _card_names(cards: list[Card]) -> str:
        return ", ".join(registry.get(c).name for c in cards) if cards else "—"

    def _half_life(days: float | None) -> str:
        return f"{days}d" if days is not None else "system default"

    lines: list[str] = [
        f"[bold]{_ROMAN[card.number]} · {card.name}[/bold]  [dim]{card.id.value}[/dim]",
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
        f"  episodic {mw.episodic:.2f}   semantic {mw.semantic:.2f}   "
        f"procedural {mw.procedural:.2f}   preference {mw.preference:.2f}",
        "",
        "[bold]Decay Half-lives[/bold]",
        f"  episodic {_half_life(dc.episodic_half_life_days)}   "
        f"semantic {_half_life(dc.semantic_half_life_days)}   "
        f"procedural {_half_life(dc.procedural_half_life_days)}   "
        f"preference {_half_life(dc.preference_half_life_days)}",
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

    console.print(Panel("\n".join(lines), title=f"🃏 {card.name}", border_style="magenta"))
