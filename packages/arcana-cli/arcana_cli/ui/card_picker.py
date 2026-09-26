"""Card selection over the renderer port.

Public API:
    card_choices()  → list[Choice[Card]]  (every card but the excluded, each with its preview panel)
    select_card()   → Card | None         (single selection)
    select_cards()  → list[Card]          (multi-select with Space)

Both pickers are :meth:`~arcana_cli.ui.renderer.Renderer.select` over
:func:`card_choices`, so they open the same two-pane picker wherever they run:
a short-lived picker app from a one-shot command (the default renderer is a
:class:`~arcana_cli.ui.renderer.TtyRenderer`), a modal screen inside the
session. THE WORLD is excluded unless a caller explicitly lets it in: it is
reserved for the meta-agent.
"""

from collections.abc import Collection

from arcana.cards.registry import get_registry
from arcana.types.card import Card
from arcana_cli.constants import ROMAN
from arcana_cli.ui.card_panel import card_panel
from arcana_cli.ui.renderer import Choice, Renderer, TtyRenderer

#: What the pickers hide unless told otherwise: THE WORLD belongs to the meta-agent alone.
RESERVED_CARDS: tuple[Card, ...] = (Card.WORLD,)


def card_choices(exclude: Collection[Card] = RESERVED_CARDS) -> list[Choice[Card]]:
    """Every card not in ``exclude``, in canonical order, labelled ``"IX. The Hermit"`` with its panel as preview."""
    registry = get_registry()
    return [
        Choice(card.id, f"{ROMAN[card.number]}. {card.name}", preview=card_panel(card, registry))
        for card in registry.all()
        if card.id not in exclude
    ]


async def select_card(
    prompt: str = "Select a card",
    *,
    initial: Card | None = None,
    exclude: Collection[Card] = RESERVED_CARDS,
    renderer: Renderer | None = None,
    flag: str | None = None,
) -> Card | None:
    """Single-card picker. Returns the chosen Card, or None if cancelled.

    Pass `initial` to pre-position the cursor on a specific card.
    Pass `exclude` to hide cards from the picker; by default only THE WORLD is
    hidden, since it's exclusive to the meta-agent.
    `renderer` is where the picker opens (a `TtyRenderer` by default); `flag`
    names the option that answers it on a surface that can't prompt.
    """
    r = renderer if renderer is not None else TtyRenderer()
    initial_values = [initial] if initial is not None else []
    return await r.select(card_choices(exclude), initial=initial_values, title=prompt, flag=flag)


async def select_cards(
    prompt: str = "Select cards",
    *,
    initial: list[Card] | None = None,
    max_items: int | None = None,
    exclude: Collection[Card] = RESERVED_CARDS,
    renderer: Renderer | None = None,
    flag: str | None = None,
) -> list[Card]:
    """Multi-card picker. Space toggles, Enter confirms. Returns empty list if cancelled.

    Pass `initial` to pre-select cards and position the cursor on the first one.
    Pass `max_items` to cap how many cards can be selected simultaneously.
    Pass `exclude` to hide specific cards from the picker (e.g. the primary
    card); THE WORLD is hidden by default, and stays hidden unless left out of
    an explicit `exclude`.
    `renderer` and `flag` are as for `select_card`.
    """
    r = renderer if renderer is not None else TtyRenderer()
    return await r.select(
        card_choices(exclude), multi=True, initial=initial or [], title=prompt, max_items=max_items, flag=flag
    )
