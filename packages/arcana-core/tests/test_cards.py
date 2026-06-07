"""Tests for the Card Engine — the soul of the system."""

import pytest

from arcana.cards.engine import CardEngine
from arcana.cards.registry import CardRegistry
from arcana.types.card import Card


@pytest.fixture
def registry():
    return CardRegistry()


@pytest.fixture
def engine(registry):
    return CardEngine(registry)


def test_registry_loads_all_defined_cards(registry):
    cards = registry.all()
    assert len(cards) >= 5  # grows as we implement more


def test_registry_get_hermit(registry):
    card = registry.get(Card.HERMIT)
    assert card.name == "The Hermit"
    assert card.archetype.default_temperature == 0.35
    assert card.archetype.memory_weights.semantic == 0.95


def test_registry_get_world_cannot_reverse(registry):
    card = registry.get(Card.WORLD)
    assert card.can_reverse is False


def test_engine_single_card_produces_config(engine):
    config = engine.resolve(Card.HERMIT)
    assert config.temperature == 0.35
    assert config.memory_weights.semantic == 0.95
    assert "Researcher" in config.system_prompt
    assert config.source_cards == [Card.HERMIT]


def test_engine_modifier_blends_temperature(engine):
    # Hermit (0.35) + Empress (0.85) modifier
    # Expected: 0.35 * 0.7 + 0.85 * 0.3 = 0.245 + 0.255 = 0.50
    config = engine.resolve(Card.HERMIT, modifiers=[Card.EMPRESS])
    assert abs(config.temperature - 0.50) < 0.01


def test_engine_config_includes_source_cards(engine):
    config = engine.resolve(Card.MAGICIAN, modifiers=[Card.FOOL])
    assert Card.MAGICIAN in config.source_cards
    assert Card.FOOL in config.source_cards


def test_engine_blend_note_describes_combination(engine):
    config = engine.resolve(Card.HERMIT, modifiers=[Card.EMPRESS])
    assert "Hermit" in config.blend_note
    assert "Empress" in config.blend_note


def test_engine_system_prompt_contains_tone(engine):
    config = engine.resolve(Card.FOOL)
    assert "enthusiastic" in config.system_prompt.lower()


def test_engine_system_prompt_mentions_modifiers(engine):
    config = engine.resolve(Card.HERMIT, modifiers=[Card.MAGICIAN])
    assert "Magician" in config.system_prompt
