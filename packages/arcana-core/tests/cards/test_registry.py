from arcana.cards.registry import CardRegistry
from arcana.types.card import Card


def test_registry_loads_all_defined_cards(registry: CardRegistry):
    cards = registry.all()
    assert len(cards) >= 5  # grows as we implement more


def test_registry_get_hermit(registry: CardRegistry):
    card = registry.get(Card.HERMIT)
    assert card.name == "The Hermit"
    assert card.archetype.default_temperature == 0.35
    assert card.archetype.memory_weights.semantic == 0.95


def test_registry_get_world_cannot_reverse(registry: CardRegistry):
    card = registry.get(Card.WORLD)
    assert card.can_reverse is False
