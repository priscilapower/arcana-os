"""Retrieval-time memory decay — a score, not a delete.

Every :class:`MemoryType` carries a :class:`DecayProfile` (a half-life, a floor,
and a consolidation threshold); every card resolves those half-lives from its
:class:`CardDecayConfig`. This module turns that configuration into a signal:
the *effective* importance of an entry at a given moment, discounted for age.

The stored ``importance`` is never mutated. ``effective_importance`` recomputes
a discounted value at read time, so a fresh, relevant memory naturally outranks
a stale one, and a fast-decaying type eventually ages out of retrieved context
without anything being written or deleted. Hard deletion is a separate,
user-visible concern (pruning / consolidation), not this module's.

Time is passed in explicitly (``now``) rather than read from the clock, so decay
is deterministic and testable without sleeping — the same seam the resilience
:class:`~arcana.memory.resilience.CircuitBreaker` uses for its cooldown.
"""

from datetime import datetime

from arcana.types import (
    DEFAULT_DECAY_PROFILES,
    WORLD_DECAY_PROFILES,
    CardDecayConfig,
    DecayProfile,
    DecayStrategy,
    MemoryEntry,
    MemoryType,
)

_SECONDS_PER_DAY = 86_400.0


def effective_importance(entry: MemoryEntry, profile: DecayProfile, now: datetime) -> float:
    """Age-discounted importance used for ranking — never the stored value.

    Exponential decay halves the contribution every ``half_life_days``:
    ``importance * 0.5 ** (age_days / half_life_days)``, floored at
    ``profile.min_importance`` so a decayed entry never reaches zero (which keeps
    a stable order among fully-aged entries). Linear decay reaches half at the
    half-life and zero at twice the half-life before the same floor applies.

    Exempt from decay — the stored importance is returned unchanged:

    * ``profile.strategy is DecayStrategy.NONE`` (e.g. The World — never forgets);
    * ``entry.pinned`` — a pin protects an entry from decay entirely;
    * a non-positive ``half_life_days`` (no meaningful curve).

    Age is measured from ``last_accessed_at`` when ``refresh_on_access`` is set
    (accessing an entry resets its decay clock) and from ``created_at`` otherwise.
    A reference timestamp in the future clamps age to zero (no negative decay).
    """
    if entry.pinned or profile.strategy is DecayStrategy.NONE or profile.half_life_days <= 0:
        return entry.importance

    reference = entry.last_accessed_at if profile.refresh_on_access else entry.created_at
    age_days = max(0.0, (now - reference).total_seconds() / _SECONDS_PER_DAY)

    if profile.strategy is DecayStrategy.LINEAR:
        decayed = entry.importance * max(0.0, 1.0 - 0.5 * age_days / profile.half_life_days)
    else:  # EXPONENTIAL — the recommended default
        decayed = entry.importance * 0.5 ** (age_days / profile.half_life_days)

    return max(profile.min_importance, decayed)


def should_consolidate(entry: MemoryEntry, profile: DecayProfile, now: datetime) -> bool:
    """Whether an entry has decayed below its consolidation threshold.

    ``True`` marks an entry as "aged out": its effective importance has fallen
    under ``profile.consolidation_threshold``. At retrieval this drops the entry
    from returned context; it is also the signal a later consolidation pass and
    pruning read to select low-value entries. Never true for a pinned entry or a
    ``NONE`` profile, which are exempt from decay.

    Note the interplay with the floor: a type whose ``min_importance`` sits at or
    above its ``consolidation_threshold`` (e.g. SEMANTIC, PROCEDURAL) can never
    age out — it persists — while a type that decays below its threshold
    (EPISODIC, PREFERENCE) eventually does.
    """
    if entry.pinned or profile.strategy is DecayStrategy.NONE:
        return False
    return effective_importance(entry, profile, now) < profile.consolidation_threshold


def decay_sorted(
    entries: list[MemoryEntry],
    profiles: dict[MemoryType, DecayProfile],
    now: datetime,
) -> list[MemoryEntry]:
    """Rank entries by decayed effective importance, dropping the aged-out ones.

    Pure and weight-agnostic: pinned entries sort first, then by effective
    importance, ties broken on recency (``last_accessed_at``). Entries that
    ``should_consolidate`` (decayed below their consolidation threshold) are
    excluded — they have aged out of retrieved context. Entries whose type has
    no profile are treated as non-decaying (ranked on their stored importance).

    Agents layer their card's per-type weighting on top of this signal in
    :meth:`~arcana.memory.router.MemoryRouter.rank`; this function is the shared,
    weightless view of the same decay computation.
    """

    def key(entry: MemoryEntry) -> tuple[bool, float, float]:
        profile = profiles.get(entry.type)
        score = entry.importance if profile is None else effective_importance(entry, profile, now)
        return (entry.pinned, score, entry.last_accessed_at.timestamp())

    kept = [e for e in entries if (p := profiles.get(e.type)) is None or not should_consolidate(e, p, now)]
    return sorted(kept, key=key, reverse=True)


def resolve_decay_profiles(decay_config: CardDecayConfig, *, world: bool = False) -> dict[MemoryType, DecayProfile]:
    """Turn a card's :class:`CardDecayConfig` into a full per-type profile map.

    Each type starts from the system default profile; the card's half-life
    overrides it where set (``None`` leaves the default). ``world=True`` returns
    the never-forgets profiles (``DecayStrategy.NONE`` on every type) — the one
    relationship with time a ``CardDecayConfig`` cannot express through
    half-lives alone.
    """
    if world:
        return {t: p.model_copy() for t, p in WORLD_DECAY_PROFILES.items()}

    overrides: dict[MemoryType, float | None] = {
        MemoryType.EPISODIC: decay_config.episodic_half_life_days,
        MemoryType.SEMANTIC: decay_config.semantic_half_life_days,
        MemoryType.PROCEDURAL: decay_config.procedural_half_life_days,
        MemoryType.PREFERENCE: decay_config.preference_half_life_days,
    }
    resolved: dict[MemoryType, DecayProfile] = {}
    for memory_type, default in DEFAULT_DECAY_PROFILES.items():
        half_life = overrides.get(memory_type)
        resolved[memory_type] = (
            default.model_copy() if half_life is None else default.model_copy(update={"half_life_days": half_life})
        )
    return resolved
