"""arcana cards — list and show tarot card definitions."""

import typer
from rich.console import Console

from arcana.cards.registry import get_registry
from arcana.types.card import Card, TarotCard
from arcana_cli.ui.card_panel import card_panel
from arcana_cli.ui.card_picker import select_card

app = typer.Typer(help="Browse the 22 Major Arcana card definitions.")
console = Console()


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
    """Browse the 22 Major Arcana — interactive two-pane picker."""
    if ctx.invoked_subcommand is not None:
        return
    picked = select_card("Browse the Major Arcana")
    if picked is not None:
        console.print(card_panel(get_registry().get(picked), get_registry()))


@app.command("show")
def show(name: str = typer.Argument(..., help="Card name or key (e.g. 'hermit', 'the-hermit')")) -> None:
    """Show full card details — prompt ingredients, memory weights, synergies."""
    card = _resolve_card(name)
    console.print(card_panel(card, get_registry()))
