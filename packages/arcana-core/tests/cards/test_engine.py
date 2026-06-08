from arcana.cards.engine import CardEngine
from arcana.types.card import Card


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
