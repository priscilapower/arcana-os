"""Shared Rich Panel renderer for a single TarotCard.

Used by `arcana cards show` and the interactive card picker preview pane.
"""

from rich.panel import Panel

from arcana.cards.registry import CardRegistry
from arcana.types.card import Card, TarotCard
from arcana_cli.constants import ROMAN


def card_panel(card: TarotCard, registry: CardRegistry) -> Panel:
    """Return a Rich Panel with full card details."""
    a = card.archetype
    pi = a.prompt_ingredients
    mw = a.memory_weights
    dc = a.decay_config

    def _card_names(card_list: list[Card]) -> str:
        return ", ".join(registry.get(c).name for c in card_list) if card_list else "—"

    def _half_life(days: float | None) -> str:
        return f"{days}d" if days is not None else "system default"

    lines: list[str] = [
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
    return Panel("\n".join(lines), title=f"🃏 {card.name}", border_style="magenta")
