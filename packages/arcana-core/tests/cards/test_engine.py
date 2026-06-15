import pytest

from arcana.cards.engine import CardEngine
from arcana.types.card import Card
from arcana.types.memory import DEFAULT_DECAY_PROFILES, MemoryType

# ---------------------------------------------------------------------------
# check_compatibility
# ---------------------------------------------------------------------------


def test_check_compatibility_no_modifiers_returns_empty(engine: CardEngine):
    compat = engine.check_compatibility(Card.FOOL, [])
    assert not compat.has_tensions
    assert not compat.has_synergies


def test_check_compatibility_detects_known_tension(engine: CardEngine):
    # Fool.tension_cards = [HERMIT, EMPEROR]
    compat = engine.check_compatibility(Card.FOOL, [Card.HERMIT])
    assert compat.has_tensions
    assert (Card.FOOL, Card.HERMIT) in compat.tensions


def test_check_compatibility_detects_known_synergy(engine: CardEngine):
    # Fool.synergy_cards = [MAGICIAN, STAR]
    compat = engine.check_compatibility(Card.FOOL, [Card.STAR])
    assert compat.has_synergies
    assert (Card.FOOL, Card.STAR) in compat.synergies


def test_check_compatibility_bidirectional_tension(engine: CardEngine):
    # Magician.tension_cards = [HIGH_PRIESTESS]; High Priestess does not list Magician
    # Still detected because the check is bidirectional
    compat = engine.check_compatibility(Card.MAGICIAN, [Card.HIGH_PRIESTESS])
    assert compat.has_tensions


def test_check_compatibility_modifier_vs_modifier_tension(engine: CardEngine):
    # Hermit.tension_cards = [FOOL, CHARIOT]; Chariot.tension_cards = [HERMIT, HANGED_MAN]
    # With EMPEROR primary, Hermit ↔ Chariot should still surface
    compat = engine.check_compatibility(Card.EMPEROR, [Card.HERMIT, Card.CHARIOT])
    tension_pairs = {frozenset(p) for p in compat.tensions}
    assert frozenset({Card.HERMIT, Card.CHARIOT}) in tension_pairs


def test_check_compatibility_no_duplicate_pairs(engine: CardEngine):
    # Fool ↔ Hermit is bidirectional, but must appear only once
    compat = engine.check_compatibility(Card.FOOL, [Card.HERMIT])
    assert len(compat.tensions) == 1


def test_check_compatibility_no_self_pairs(engine: CardEngine):
    # Primary stripped from modifiers in CLI, but guard at engine level too
    compat = engine.check_compatibility(Card.FOOL, [Card.STAR, Card.MAGICIAN])
    all_pairs = compat.tensions + compat.synergies
    for a, b in all_pairs:
        assert a != b


def test_engine_single_card_produces_config(engine: CardEngine):
    config = engine.resolve(Card.HERMIT)
    assert config.temperature == 0.35
    assert config.memory_weights.semantic == 0.95
    assert "Researcher" in config.system_prompt
    assert config.source_cards == [Card.HERMIT]


def test_engine_modifier_blends_temperature(engine: CardEngine):
    # Hermit (0.35) + Empress (0.85) modifier
    # Expected: 0.35 * 0.7 + 0.85 * 0.3 = 0.245 + 0.255 = 0.50
    config = engine.resolve(Card.HERMIT, modifiers=[Card.EMPRESS])
    assert abs(config.temperature - 0.50) < 0.01


def test_engine_config_includes_source_cards(engine: CardEngine):
    config = engine.resolve(Card.MAGICIAN, modifiers=[Card.FOOL])
    assert Card.MAGICIAN in config.source_cards
    assert Card.FOOL in config.source_cards


def test_engine_blend_note_describes_combination(engine: CardEngine):
    config = engine.resolve(Card.HERMIT, modifiers=[Card.EMPRESS])
    assert "Hermit" in config.blend_note
    assert "Empress" in config.blend_note


def test_engine_system_prompt_contains_tone(engine: CardEngine):
    config = engine.resolve(Card.FOOL)
    assert "enthusiastic" in config.system_prompt.lower()


def test_engine_system_prompt_mentions_modifiers(engine: CardEngine):
    config = engine.resolve(Card.HERMIT, modifiers=[Card.MAGICIAN])
    assert "Magician" in config.system_prompt


# ---------------------------------------------------------------------------
# Three-card blending
# ---------------------------------------------------------------------------


def test_engine_three_card_blend_temperature(engine: CardEngine):
    # Hermit (0.35) primary + Fool (0.95) + Empress (0.85) modifiers
    # mod_weight = 0.3 / 2 = 0.15 each
    # Expected: 0.35 * 0.7 + 0.95 * 0.15 + 0.85 * 0.15
    #         = 0.245 + 0.1425 + 0.1275 = 0.515
    config = engine.resolve(Card.HERMIT, modifiers=[Card.FOOL, Card.EMPRESS])
    expected = 0.35 * 0.7 + 0.95 * 0.15 + 0.85 * 0.15
    assert abs(config.temperature - expected) < 0.01


def test_engine_three_card_blend_source_cards(engine: CardEngine):
    config = engine.resolve(Card.HERMIT, modifiers=[Card.FOOL, Card.EMPRESS])
    assert config.source_cards == [Card.HERMIT, Card.FOOL, Card.EMPRESS]


def test_engine_three_card_blend_note_mentions_all(engine: CardEngine):
    config = engine.resolve(Card.HERMIT, modifiers=[Card.FOOL, Card.EMPRESS])
    assert "Hermit" in config.blend_note
    assert "Fool" in config.blend_note
    assert "Empress" in config.blend_note


def test_engine_three_card_blend_memory_weights(engine: CardEngine):
    # Verify blended weights are non-negative and approximately reasonable
    config = engine.resolve(Card.MAGICIAN, modifiers=[Card.HERMIT, Card.EMPRESS])
    mw = config.memory_weights
    assert mw.episodic >= 0.0
    assert mw.semantic >= 0.0
    assert mw.procedural >= 0.0
    assert mw.preference >= 0.0


# ---------------------------------------------------------------------------
# Decay config blending
# ---------------------------------------------------------------------------


def test_engine_single_card_decay_config_copied(engine: CardEngine):
    config = engine.resolve(Card.FOOL)
    assert config.decay_config.episodic_half_life_days == 3.0
    assert config.decay_config.semantic_half_life_days == 30.0


def test_engine_modifier_blends_decay_episodic(engine: CardEngine):
    # Fool episodic = 3.0, Hermit has None (uses default 14.0)
    # Hermit primary (70%) + Fool modifier (30%)
    hermit_episodic = engine._registry.get(Card.HERMIT).archetype.decay_config.episodic_half_life_days
    default_episodic = DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC].half_life_days
    p_val = hermit_episodic if hermit_episodic is not None else default_episodic
    expected = round(p_val * 0.7 + 3.0 * 0.3, 1)

    config = engine.resolve(Card.HERMIT, modifiers=[Card.FOOL])
    assert abs(config.decay_config.episodic_half_life_days - expected) < 0.11


def test_engine_none_half_life_falls_back_to_default(engine: CardEngine):
    # Find a card with None decay values to test the fallback
    registry = engine._registry
    cards_with_none_decay = [c for c in registry.all() if c.archetype.decay_config.episodic_half_life_days is None]
    if not cards_with_none_decay:
        pytest.skip("No cards with None episodic_half_life_days in current definitions")

    primary_card = cards_with_none_decay[0]
    config = engine.resolve(primary_card.id, modifiers=[Card.FOOL])
    # Result should be a valid float, not None — default was applied
    assert config.decay_config.episodic_half_life_days is not None
    assert config.decay_config.episodic_half_life_days > 0


def test_engine_three_card_decay_blend(engine: CardEngine):
    # With three cards the per-modifier weight is 0.15 each
    config = engine.resolve(Card.FOOL, modifiers=[Card.HERMIT, Card.MAGICIAN])
    # Just verify the output is a valid positive float
    dc = config.decay_config
    assert dc.episodic_half_life_days is not None and dc.episodic_half_life_days > 0
    assert dc.semantic_half_life_days is not None and dc.semantic_half_life_days > 0


# ---------------------------------------------------------------------------
# World as primary card
# ---------------------------------------------------------------------------


def test_engine_rejects_more_than_two_modifiers(engine: CardEngine):
    with pytest.raises(ValueError, match="Too many modifier cards"):
        engine.resolve(Card.FOOL, modifiers=[Card.HERMIT, Card.STAR, Card.MAGICIAN])


def test_engine_accepts_exactly_two_modifiers(engine: CardEngine):
    config = engine.resolve(Card.FOOL, modifiers=[Card.HERMIT, Card.STAR])
    assert len(config.source_cards) == 3


def test_engine_world_primary_produces_valid_config(engine: CardEngine):
    config = engine.resolve(Card.WORLD)
    assert config.source_cards == [Card.WORLD]
    assert "Meta-Agent" in config.system_prompt  # WORLD's role
    assert 0.0 <= config.temperature <= 1.0


def test_engine_world_with_modifier_blends_without_error(engine: CardEngine):
    config = engine.resolve(Card.WORLD, modifiers=[Card.HERMIT])
    assert Card.WORLD in config.source_cards
    assert Card.HERMIT in config.source_cards
