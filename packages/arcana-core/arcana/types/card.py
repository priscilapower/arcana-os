"""TarotCard and related types — the soul of every agent."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Card(StrEnum):
    """The 22 Major Arcana. Assign one to any agent."""

    FOOL = "the-fool"
    MAGICIAN = "the-magician"
    HIGH_PRIESTESS = "the-high-priestess"
    EMPRESS = "the-empress"
    EMPEROR = "the-emperor"
    HIEROPHANT = "the-hierophant"
    LOVERS = "the-lovers"
    CHARIOT = "the-chariot"
    STRENGTH = "strength"
    HERMIT = "the-hermit"
    WHEEL_OF_FORTUNE = "wheel-of-fortune"
    JUSTICE = "justice"
    HANGED_MAN = "the-hanged-man"
    DEATH = "death"
    TEMPERANCE = "temperance"
    DEVIL = "the-devil"
    TOWER = "the-tower"
    STAR = "the-star"
    MOON = "the-moon"
    SUN = "the-sun"
    JUDGEMENT = "judgement"
    WORLD = "the-world"


class PromptIngredients(BaseModel):
    tone: str
    approach: str
    priorities: list[str]
    avoid: list[str]


class MemoryWeights(BaseModel):
    """How strongly an agent weights each memory type. Values 0.0–1.0."""

    episodic: float = 0.5
    semantic: float = 0.5
    procedural: float = 0.5
    preference: float = 0.5


class CardDecayConfig(BaseModel):
    """
    Card-specific decay half-lives (days).
    None means use the system default for that type.

    These express each card's relationship with time and memory:
      - High Priestess remembers everything (very long half-lives)
      - Fool lives in the present (very short half-lives)
      - The World never forgets (strategy=NONE on all types)
    """

    episodic_half_life_days: float | None = None
    semantic_half_life_days: float | None = None
    procedural_half_life_days: float | None = None
    preference_half_life_days: float | None = None


class CardArchetype(BaseModel):
    """The functional definition of a card's behaviour."""

    role: str
    core_traits: list[str]
    prompt_ingredients: PromptIngredients
    default_temperature: float
    memory_weights: MemoryWeights
    decay_config: CardDecayConfig = CardDecayConfig()
    preferred_tool_categories: list[str] = []


class TarotCard(BaseModel):
    """A single card definition."""

    id: Card
    name: str
    number: int
    archetype: CardArchetype

    reversed_meaning: str
    reversed_trigger: str

    imagery: str
    color_palette: list[str]

    synergy_cards: list[Card] = []
    tension_cards: list[Card] = []
    can_reverse: bool = True

    def is_world(self) -> bool:
        return self.id == Card.WORLD


class AgentConfig(BaseModel):
    """Resolved config from CardEngine. Applied to Agent at creation."""

    system_prompt: str
    temperature: float
    memory_weights: MemoryWeights
    decay_config: CardDecayConfig = CardDecayConfig()
    suggested_skill_ids: list[str] = []
    source_cards: list[Card] = []
    blend_note: str = ""
