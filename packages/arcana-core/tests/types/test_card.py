from arcana.types.card import (
    AgentConfig,
    Card,
    CardArchetype,
    CardDecayConfig,
    MemoryWeights,
    PromptIngredients,
    TarotCard,
)


def test_memory_weights_defaults():
    w = MemoryWeights()
    assert w.episodic == 0.5
    assert w.semantic == 0.5
    assert w.procedural == 0.5
    assert w.preference == 0.5


def test_memory_weights_custom():
    w = MemoryWeights(semantic=0.95, episodic=0.2)
    assert w.semantic == 0.95
    assert w.episodic == 0.2


def test_card_decay_config_all_none_by_default():
    cfg = CardDecayConfig()
    assert cfg.episodic_half_life_days is None
    assert cfg.semantic_half_life_days is None
    assert cfg.procedural_half_life_days is None
    assert cfg.preference_half_life_days is None


def test_card_decay_config_set():
    cfg = CardDecayConfig(episodic_half_life_days=7.0, semantic_half_life_days=365.0)
    assert cfg.episodic_half_life_days == 7.0
    assert cfg.semantic_half_life_days == 365.0


def test_prompt_ingredients_construction():
    pi = PromptIngredients(
        tone="curious",
        approach="methodical",
        priorities=["depth", "accuracy"],
        avoid=["vague claims"],
    )
    assert pi.tone == "curious"
    assert "depth" in pi.priorities
    assert "vague claims" in pi.avoid


def test_card_archetype_defaults():
    archetype = CardArchetype(
        role="Researcher",
        core_traits=["analytical"],
        prompt_ingredients=PromptIngredients(tone="precise", approach="deep", priorities=[], avoid=[]),
        default_temperature=0.35,
        memory_weights=MemoryWeights(semantic=0.95),
    )
    assert archetype.decay_config == CardDecayConfig()
    assert archetype.preferred_tool_categories == []


def test_tarot_card_is_world_true():
    card = _make_tarot_card(Card.WORLD)
    assert card.is_world() is True


def test_tarot_card_is_world_false():
    card = _make_tarot_card(Card.HERMIT)
    assert card.is_world() is False


def test_tarot_card_can_reverse_default_true():
    card = _make_tarot_card(Card.FOOL)
    assert card.can_reverse is True


def test_tarot_card_can_reverse_false():
    card = _make_tarot_card(Card.WORLD, can_reverse=False)
    assert card.can_reverse is False


def test_tarot_card_synergy_and_tension_default_empty():
    card = _make_tarot_card(Card.MAGICIAN)
    assert card.synergy_cards == []
    assert card.tension_cards == []


def test_agent_config_defaults():
    config = AgentConfig(
        system_prompt="Be helpful.",
        temperature=0.5,
        memory_weights=MemoryWeights(),
    )
    assert config.suggested_skill_ids == []
    assert config.source_cards == []
    assert config.blend_note == ""
    assert config.decay_config == CardDecayConfig()


def test_agent_config_with_source_cards():
    config = AgentConfig(
        system_prompt="Be helpful.",
        temperature=0.5,
        memory_weights=MemoryWeights(),
        source_cards=[Card.HERMIT, Card.EMPRESS],
        blend_note="Hermit + Empress",
    )
    assert Card.HERMIT in config.source_cards
    assert "Empress" in config.blend_note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tarot_card(card_id: Card, **kwargs) -> TarotCard:
    archetype = CardArchetype(
        role="Test",
        core_traits=[],
        prompt_ingredients=PromptIngredients(tone="neutral", approach="direct", priorities=[], avoid=[]),
        default_temperature=0.5,
        memory_weights=MemoryWeights(),
    )
    defaults = dict(
        id=card_id,
        name=card_id.value,
        number=0,
        archetype=archetype,
        reversed_meaning="reversed",
        reversed_trigger="error",
        imagery="none",
        color_palette=[],
    )
    return TarotCard(**{**defaults, **kwargs})
