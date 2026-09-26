"""arcana cards — list and show tarot card definitions.

The command bodies are renderer-agnostic coroutines (``list_cards``,
``show_card``) that take a :class:`~arcana_cli.ui.renderer.Renderer`; the Typer
callbacks only pick the renderer and run the coroutine.
"""

import typer

from arcana.cards.registry import get_registry
from arcana.types.card import Card, TarotCard
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_ERROR
from arcana_cli.ui.card_panel import card_panel
from arcana_cli.ui.card_picker import select_card
from arcana_cli.ui.renderer import Renderer, renderer_for
from arcana_cli.ui.theme import err, warn

app = typer.Typer(help="Browse the 22 Major Arcana card definitions.")


def _resolve_card(r: Renderer, name: str) -> TarotCard:
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
        r.emit(warn(f"Ambiguous: {', '.join(c.name for c in matches)}"))
        raise typer.Exit(EXIT_ERROR)
    r.emit(err(f"Unknown card: {name!r}"))
    raise typer.Exit(EXIT_ERROR)


#: Names what answers the browse picker when there is no terminal to show it on.
BROWSE_INSTEAD = "a card name to 'arcana cards show'"


async def list_cards(r: Renderer) -> None:
    """Offer every card but THE WORLD (reserved for the meta-agent) and show the one picked."""
    picked = await select_card("Browse the Major Arcana", renderer=r, flag=BROWSE_INSTEAD)
    if picked is not None:
        registry = get_registry()
        r.emit(card_panel(registry.get(picked), registry))


async def show_card(r: Renderer, name: str) -> None:
    """Show one card, resolved by key, short key, or unique name fragment."""
    r.emit(card_panel(_resolve_card(r, name), get_registry()))


@app.callback(invoke_without_command=True)
def list_cmd(ctx: typer.Context) -> None:
    """Browse the 22 Major Arcana — interactive two-pane picker."""
    if ctx.invoked_subcommand is not None:
        return
    run_async(list_cards(renderer_for(json=False)))


@app.command("show")
def show_cmd(name: str = typer.Argument(..., help="Card name or key (e.g. 'hermit', 'the-hermit')")) -> None:
    """Show full card details — prompt ingredients, memory weights, synergies."""
    run_async(show_card(renderer_for(json=False), name))
