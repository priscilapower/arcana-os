"""
CardEngine — translates TarotCard assignments into concrete AgentConfig.

Blending rules:
  Primary card  → 70% weight
  Modifier cards → 30% weight split equally

Produces: system_prompt, temperature, memory_weights, decay_config, suggested_skills.
"""

from __future__ import annotations

from arcana.cards.registry import CardRegistry
from arcana.types.card import (
    AgentConfig,
    Card,
    CardDecayConfig,
    MemoryWeights,
    TarotCard,
)
from arcana.types.memory import DEFAULT_DECAY_PROFILES, MemoryType


class CardEngine:
    PRIMARY_WEIGHT = 0.7
    MODIFIER_TOTAL_WEIGHT = 0.3

    def __init__(self, registry: CardRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        primary: Card,
        modifiers: list[Card] | None = None,
    ) -> AgentConfig:
        modifiers = modifiers or []
        primary_card = self._registry.get(primary)
        modifier_cards = [self._registry.get(m) for m in modifiers]

        return AgentConfig(
            system_prompt=self._build_system_prompt(primary_card, modifier_cards),
            temperature=round(self._blend_temperature(primary_card, modifier_cards), 2),
            memory_weights=self._blend_memory_weights(primary_card, modifier_cards),
            decay_config=self._blend_decay_config(primary_card, modifier_cards),
            source_cards=[primary, *modifiers],
            blend_note=self._describe_blend(primary_card, modifier_cards),
        )

    # ------------------------------------------------------------------

    def _build_system_prompt(self, primary: TarotCard, modifiers: list[TarotCard]) -> str:
        pi = primary.archetype.prompt_ingredients
        lines = [
            f"You are {primary.archetype.role}.",
            "",
            f"Tone: {pi.tone}",
            f"Approach: {pi.approach}",
            "",
            "Priorities:",
            *[f"- {p}" for p in pi.priorities],
            "",
            "Avoid:",
            *[f"- {a}" for a in pi.avoid],
        ]
        if modifiers:
            lines += ["", "Additional influences:"]
            for mod in modifiers:
                mpi = mod.archetype.prompt_ingredients
                lines.append(f"- From {mod.name}: {mpi.tone}. {mpi.approach}")
        return "\n".join(lines)

    def _blend_temperature(self, primary: TarotCard, modifiers: list[TarotCard]) -> float:
        if not modifiers:
            return primary.archetype.default_temperature
        mod_weight = self.MODIFIER_TOTAL_WEIGHT / len(modifiers)
        result = primary.archetype.default_temperature * self.PRIMARY_WEIGHT
        for mod in modifiers:
            result += mod.archetype.default_temperature * mod_weight
        return result

    def _blend_memory_weights(self, primary: TarotCard, modifiers: list[TarotCard]) -> MemoryWeights:
        if not modifiers:
            return primary.archetype.memory_weights.model_copy()
        mod_weight = self.MODIFIER_TOTAL_WEIGHT / len(modifiers)
        pw = primary.archetype.memory_weights
        result = {
            "episodic": pw.episodic * self.PRIMARY_WEIGHT,
            "semantic": pw.semantic * self.PRIMARY_WEIGHT,
            "procedural": pw.procedural * self.PRIMARY_WEIGHT,
            "preference": pw.preference * self.PRIMARY_WEIGHT,
        }
        for mod in modifiers:
            mw = mod.archetype.memory_weights
            result["episodic"] += mw.episodic * mod_weight
            result["semantic"] += mw.semantic * mod_weight
            result["procedural"] += mw.procedural * mod_weight
            result["preference"] += mw.preference * mod_weight
        return MemoryWeights(**{k: round(v, 2) for k, v in result.items()})

    def _blend_decay_config(self, primary: TarotCard, modifiers: list[TarotCard]) -> CardDecayConfig:
        """Blend half-life values using the same primary/modifier weighting."""
        if not modifiers:
            return primary.archetype.decay_config.model_copy()

        def resolve_half_life(field: str, default: float) -> float:
            p_val = getattr(primary.archetype.decay_config, field) or default
            result = p_val * self.PRIMARY_WEIGHT
            mod_weight = self.MODIFIER_TOTAL_WEIGHT / len(modifiers)
            for mod in modifiers:
                m_val = getattr(mod.archetype.decay_config, field) or default
                result += m_val * mod_weight
            return round(result, 1)

        return CardDecayConfig(
            episodic_half_life_days=resolve_half_life(
                "episodic_half_life_days",
                DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC].half_life_days,
            ),
            semantic_half_life_days=resolve_half_life(
                "semantic_half_life_days",
                DEFAULT_DECAY_PROFILES[MemoryType.SEMANTIC].half_life_days,
            ),
            procedural_half_life_days=resolve_half_life(
                "procedural_half_life_days",
                DEFAULT_DECAY_PROFILES[MemoryType.PROCEDURAL].half_life_days,
            ),
            preference_half_life_days=resolve_half_life(
                "preference_half_life_days",
                DEFAULT_DECAY_PROFILES[MemoryType.PREFERENCE].half_life_days,
            ),
        )

    def _describe_blend(self, primary: TarotCard, modifiers: list[TarotCard]) -> str:
        if not modifiers:
            return f"{primary.name}: {primary.archetype.role}"
        names = " + ".join([primary.name, *[m.name for m in modifiers]])
        return f"{names}: blended archetype"
