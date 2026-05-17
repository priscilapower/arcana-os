"""All 22 card definitions. Add new cards here."""

from arcana.types.card import TarotCard

from arcana.cards.definitions.fool import FOOL
from arcana.cards.definitions.magician import MAGICIAN
from arcana.cards.definitions.high_priestess import HIGH_PRIESTESS
from arcana.cards.definitions.hermit import HERMIT
from arcana.cards.definitions.world import WORLD


def all_cards() -> list[TarotCard]:
    """
    Returns all defined cards.
    Add cards to this list as they are implemented.
    Remaining 17 cards are scaffolded as TODOs — implement in Epic 2.
    """
    return [
        FOOL,
        MAGICIAN,
        HIGH_PRIESTESS,
        HERMIT,
        WORLD,
        # TODO: EMPRESS, EMPEROR, HIEROPHANT, LOVERS, CHARIOT,
        # TODO: STRENGTH, WHEEL_OF_FORTUNE, JUSTICE, HANGED_MAN,
        # TODO: DEATH, TEMPERANCE, DEVIL, TOWER, STAR, MOON, SUN, JUDGEMENT
    ]
