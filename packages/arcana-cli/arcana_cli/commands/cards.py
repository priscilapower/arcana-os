"""arcana cards — list and show tarot card definitions.

The command bodies are renderer-agnostic coroutines (``list_cards``,
``card_catalog``, ``show_card``) that take a
:class:`~arcana_cli.ui.renderer.Renderer`; the Typer callbacks only pick the
renderer and run the coroutine. ``arcana cards`` browses with the two-pane
picker; under ``--json`` there is nothing to browse with, so it emits the
catalog instead.
"""

from typing import Any

import typer

from arcana.cards.registry import get_registry
from arcana.types.card import Card, TarotCard
from arcana_cli._async import run_async
from arcana_cli.command_impl import command_impl
from arcana_cli.constants import ROMAN
from arcana_cli.ui.card_panel import card_panel
from arcana_cli.ui.card_picker import select_card
from arcana_cli.ui.renderer import Renderer, View, fail, renderer_for
from arcana_cli.ui.theme import TXT3, make_table, warn

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
        fail(r, f"Ambiguous: {', '.join(c.name for c in matches)}", headline=warn)
    fail(r, f"Unknown card: {name!r}")


def _card_summary(card: TarotCard) -> dict[str, Any]:
    """The ``--json`` shape of one card in the catalog."""
    return {"id": card.id.value, "number": card.number, "name": card.name, "role": card.archetype.role}


def _card_view(card: TarotCard) -> View:
    """One card: its panel, or its full definition."""
    return View(card_panel(card, get_registry()), card.model_dump(mode="json"))


#: Names what answers the browse picker when there is no terminal to show it on.
BROWSE_INSTEAD = "a card name to 'arcana cards show'"


@command_impl("cards")
async def list_cards(r: Renderer) -> None:
    """Offer every card but THE WORLD (reserved for the meta-agent) and show the one picked."""
    picked = await select_card("Browse the Major Arcana", renderer=r, flag=BROWSE_INSTEAD)
    if picked is not None:
        r.emit(_card_view(get_registry().get(picked)))


async def card_catalog(r: Renderer) -> None:
    """Every card but THE WORLD, in order: a table, or an array of card summaries."""
    cards = [c for c in get_registry().all() if c.id != Card.WORLD]
    table = make_table("Major Arcana")
    table.add_column("#", justify="right")
    table.add_column("Card", style="bold")
    table.add_column("Key", style=TXT3)
    table.add_column("Role")
    for c in cards:
        table.add_row(ROMAN[c.number], c.name, c.id.value, c.archetype.role)
    r.emit(View(table, [_card_summary(c) for c in cards]))


@command_impl("cards show")
async def show_card(r: Renderer, name: str) -> None:
    """Show one card, resolved by key, short key, or unique name fragment."""
    r.emit(_card_view(_resolve_card(r, name)))


@app.callback(invoke_without_command=True)
def list_cmd(
    ctx: typer.Context,
    json_: bool = typer.Option(False, "--json", help="Emit the card catalog as JSON instead of browsing"),
) -> None:
    """Browse the 22 Major Arcana — interactive two-pane picker."""
    if ctx.invoked_subcommand is not None:
        return
    r = renderer_for(json_)
    run_async(card_catalog(r) if json_ else list_cards(r))


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Card name or key (e.g. 'hermit', 'the-hermit')"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show full card details — prompt ingredients, memory weights, synergies."""
    run_async(show_card(renderer_for(json_), name))
