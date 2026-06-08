"""CardRegistry — loads and provides access to all 22 card definitions."""

from functools import lru_cache

from arcana.types.card import Card, TarotCard


class CardRegistry:
    """
    Holds all 22 Major Arcana definitions.
    Cards are loaded lazily from cards/definitions/*.py on first access.
    """

    def __init__(self) -> None:
        self._cards: dict[Card, TarotCard] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        # Import all definition modules; each calls registry.register()
        from arcana.cards.definitions import all_cards

        for card in all_cards():
            self._cards[card.id] = card
        self._loaded = True

    def get(self, card_id: Card) -> TarotCard:
        self._load()
        if card_id not in self._cards:
            raise ValueError(f"Unknown card: {card_id}")
        return self._cards[card_id]

    def all(self) -> list[TarotCard]:
        self._load()
        return list(self._cards.values())

    def list_ids(self) -> list[Card]:
        self._load()
        return list(self._cards.keys())


@lru_cache(maxsize=1)
def get_registry() -> CardRegistry:
    """Global singleton registry. Use this everywhere."""
    return CardRegistry()
