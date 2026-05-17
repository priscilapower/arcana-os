"""
Memory decay — effective importance scoring.

Decay doesn't delete anything. It reduces the effective score of
entries at retrieval time, so fresh relevant memories naturally
surface above stale ones. Deletion only happens during consolidation
(The World) or explicit user action.

Usage:
    from arcana.memory.decay import effective_importance, should_consolidate

    score = effective_importance(entry, profile, now=datetime.utcnow())
    if should_consolidate(score, profile):
        # candidate for The World's consolidation pass
"""

from __future__ import annotations

import math
from datetime import datetime

from arcana.types.memory import (
    DecayProfile,
    DecayStrategy,
    MemoryEntry,
    MemoryProfile,
    MemoryType,
    DEFAULT_DECAY_PROFILES,
)


def effective_importance(
    entry: MemoryEntry,
    profile: DecayProfile | None = None,
    now: datetime | None = None,
) -> float:
    """
    Compute the effective importance of a memory entry at a given time.

    Pinned entries always return their base importance.
    Consolidated entries (summaries) decay more slowly than originals.

    Args:
        entry: The memory entry to score.
        profile: Decay profile to use. Falls back to type default.
        now: Reference time. Defaults to utcnow().

    Returns:
        Float in [min_importance, 1.0].
    """
    if entry.pinned:
        return entry.importance

    now = now or datetime.utcnow()
    profile = profile or DEFAULT_DECAY_PROFILES[entry.type]

    if profile.strategy == DecayStrategy.NONE:
        return entry.importance

    # Use last_accessed_at if refresh_on_access, else created_at
    reference_time = (
        entry.last_accessed_at if profile.refresh_on_access else entry.created_at
    )
    age_days = max(0.0, (now - reference_time).total_seconds() / 86_400)

    if profile.strategy == DecayStrategy.EXPONENTIAL:
        # Standard exponential decay: I * 0.5^(age / half_life)
        decay_factor = 0.5 ** (age_days / max(profile.half_life_days, 0.01))
    elif profile.strategy == DecayStrategy.LINEAR:
        # Linear decay to zero over 2 * half_life days
        decay_factor = max(0.0, 1.0 - (age_days / (2 * profile.half_life_days)))
    else:
        decay_factor = 1.0

    # Consolidated summaries decay 50% slower than originals
    if entry.is_consolidated:
        decay_factor = decay_factor ** 0.5

    decayed = entry.importance * decay_factor
    return round(max(decayed, profile.min_importance), 4)


def should_consolidate(
    entry: MemoryEntry,
    profile: DecayProfile | None = None,
    now: datetime | None = None,
) -> bool:
    """
    Return True if an entry's effective importance has dropped below
    the consolidation threshold — making it a candidate for The World
    to summarise and archive.
    """
    if entry.pinned or entry.is_consolidated:
        return False
    profile = profile or DEFAULT_DECAY_PROFILES[entry.type]
    score = effective_importance(entry, profile, now)
    return score < profile.consolidation_threshold


def decay_sorted(
    entries: list[MemoryEntry],
    memory_profile: MemoryProfile | None = None,
    now: datetime | None = None,
) -> list[MemoryEntry]:
    """
    Sort entries by effective importance (descending).
    Used by MemoryFederation.search() to rank results.
    """
    now = now or datetime.utcnow()

    def score(entry: MemoryEntry) -> float:
        if memory_profile:
            profile = memory_profile.decay_profiles.get(
                entry.type, DEFAULT_DECAY_PROFILES[entry.type]
            )
        else:
            profile = DEFAULT_DECAY_PROFILES[entry.type]
        return effective_importance(entry, profile, now)

    return sorted(entries, key=score, reverse=True)


def consolidation_candidates(
    entries: list[MemoryEntry],
    memory_profile: MemoryProfile | None = None,
    now: datetime | None = None,
) -> list[MemoryEntry]:
    """Return entries that have decayed below their consolidation threshold."""
    now = now or datetime.utcnow()
    candidates = []
    for entry in entries:
        profile = None
        if memory_profile:
            profile = memory_profile.decay_profiles.get(entry.type)
        if should_consolidate(entry, profile, now):
            candidates.append(entry)
    return candidates
