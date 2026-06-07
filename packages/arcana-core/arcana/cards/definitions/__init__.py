"""All 22 card definitions. Add new cards here."""

from arcana.cards.definitions.chariot import CHARIOT
from arcana.cards.definitions.death import DEATH
from arcana.cards.definitions.devil import DEVIL
from arcana.cards.definitions.emperor import EMPEROR
from arcana.cards.definitions.empress import EMPRESS
from arcana.cards.definitions.fool import FOOL
from arcana.cards.definitions.hanged_man import HANGED_MAN
from arcana.cards.definitions.hermit import HERMIT
from arcana.cards.definitions.hierophant import HIEROPHANT
from arcana.cards.definitions.high_priestess import HIGH_PRIESTESS
from arcana.cards.definitions.judgement import JUDGEMENT
from arcana.cards.definitions.justice import JUSTICE
from arcana.cards.definitions.lovers import LOVERS
from arcana.cards.definitions.magician import MAGICIAN
from arcana.cards.definitions.moon import MOON
from arcana.cards.definitions.star import STAR
from arcana.cards.definitions.strength import STRENGTH
from arcana.cards.definitions.sun import SUN
from arcana.cards.definitions.temperance import TEMPERANCE
from arcana.cards.definitions.tower import TOWER
from arcana.cards.definitions.wheel_of_fortune import WHEEL_OF_FORTUNE
from arcana.cards.definitions.world import WORLD
from arcana.types.card import TarotCard


def all_cards() -> list[TarotCard]:
    """Returns all 22 Major Arcana definitions in canonical order."""
    return [
        FOOL,
        MAGICIAN,
        HIGH_PRIESTESS,
        EMPRESS,
        EMPEROR,
        HIEROPHANT,
        LOVERS,
        CHARIOT,
        STRENGTH,
        HERMIT,
        WHEEL_OF_FORTUNE,
        JUSTICE,
        HANGED_MAN,
        DEATH,
        TEMPERANCE,
        DEVIL,
        TOWER,
        STAR,
        MOON,
        SUN,
        JUDGEMENT,
        WORLD,
    ]
