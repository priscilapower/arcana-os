"""Integration tests: all 22 card definitions loaded correctly with no gaps or conflicts."""

import pytest

from arcana.cards.definitions import all_cards
from arcana.cards.registry import CardRegistry
from arcana.types.card import Card


@pytest.fixture(scope="module")
def registry():
    r = CardRegistry()
    return r


@pytest.fixture(scope="module")
def cards():
    return all_cards()


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def test_all_22_cards_present(cards):
    assert len(cards) == 22


def test_registry_loads_all_22_cards(registry):
    assert len(registry.all()) == 22


def test_all_enum_values_are_registered(registry):
    for card in list(Card):
        tarot = registry.get(card)
        assert tarot is not None, f"Card.{card.name} missing from registry"


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_no_duplicate_card_ids(cards):
    ids = [c.id for c in cards]
    assert len(ids) == len(set(ids)), "Duplicate card IDs found"


def test_no_duplicate_card_numbers(cards):
    numbers = [c.number for c in cards]
    assert len(numbers) == len(set(numbers)), "Duplicate card numbers found"


def test_no_duplicate_card_names(cards):
    names = [c.name for c in cards]
    assert len(names) == len(set(names)), "Duplicate card names found"


# ---------------------------------------------------------------------------
# ID consistency
# ---------------------------------------------------------------------------


def test_card_id_matches_enum_value(cards):
    for card in cards:
        assert isinstance(card.id, Card), f"{card.name}: id is not a Card enum"


def test_all_cards_in_canonical_order_cover_all_enum_values(cards):
    card_ids = {c.id for c in cards}
    enum_ids = {c for c in list(Card)}
    assert card_ids == enum_ids


# ---------------------------------------------------------------------------
# Numbers — The Fool is 0, The World is 21
# ---------------------------------------------------------------------------


def test_fool_is_number_zero(registry):
    assert registry.get(Card.FOOL).number == 0


def test_world_is_number_21(registry):
    assert registry.get(Card.WORLD).number == 21


def test_cards_numbered_zero_through_21(cards):
    numbers = sorted(c.number for c in cards)
    assert numbers == list(range(22))


# ---------------------------------------------------------------------------
# Temperature ranges
# ---------------------------------------------------------------------------


def test_all_temperatures_in_valid_range(cards):
    for card in cards:
        temp = card.archetype.default_temperature
        assert 0.0 <= temp <= 1.0, f"{card.name}: temperature {temp} out of range"


# ---------------------------------------------------------------------------
# Prompt ingredients not empty
# ---------------------------------------------------------------------------


def test_all_cards_have_non_empty_roles(cards):
    for card in cards:
        assert card.archetype.role.strip(), f"{card.name}: role is empty"


def test_all_cards_have_non_empty_prompt_priorities(cards):
    for card in cards:
        pi = card.archetype.prompt_ingredients
        assert pi.priorities, f"{card.name}: priorities list is empty"
        assert pi.avoid, f"{card.name}: avoid list is empty"


# ---------------------------------------------------------------------------
# can_reverse
# ---------------------------------------------------------------------------


def test_world_cannot_reverse(registry):
    assert registry.get(Card.WORLD).can_reverse is False


def test_non_world_cards_can_reverse(cards):
    for card in cards:
        if card.id != Card.WORLD:
            assert card.can_reverse is True, f"{card.name}: expected can_reverse=True"


# ---------------------------------------------------------------------------
# Synergy / tension cards reference valid enum values
# ---------------------------------------------------------------------------


def test_all_synergy_cards_are_valid_enum_values(cards):
    valid = {c for c in list(Card)}
    for card in cards:
        for ref in card.synergy_cards:
            assert ref in valid, f"{card.name}: synergy card '{ref}' is not a valid Card enum value"


def test_all_tension_cards_are_valid_enum_values(cards):
    valid = {c for c in list(Card)}
    for card in cards:
        for ref in card.tension_cards:
            assert ref in valid, f"{card.name}: tension card '{ref}' is not a valid Card enum value"


def test_no_card_synergy_with_itself(cards):
    for card in cards:
        assert card.id not in card.synergy_cards, f"{card.name}: appears in its own synergy_cards"


def test_no_card_tension_with_itself(cards):
    for card in cards:
        assert card.id not in card.tension_cards, f"{card.name}: appears in its own tension_cards"


# ---------------------------------------------------------------------------
# Memory weights sum check (soft sanity)
# ---------------------------------------------------------------------------


def test_all_memory_weight_components_non_negative(cards):
    for card in cards:
        mw = card.archetype.memory_weights
        for field_name in ("episodic", "semantic", "procedural", "preference"):
            val = getattr(mw, field_name)
            assert val >= 0.0, f"{card.name}: memory_weight.{field_name} is negative"
