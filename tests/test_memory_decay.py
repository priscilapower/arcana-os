"""Tests for memory decay logic."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from arcana.memory.decay import (
    consolidation_candidates,
    decay_sorted,
    effective_importance,
    should_consolidate,
)
from arcana.types.memory import (
    DEFAULT_DECAY_PROFILES,
    DecayProfile,
    DecayStrategy,
    MemoryEntry,
    MemoryType,
)


def make_entry(
    type: MemoryType = MemoryType.EPISODIC,
    importance: float = 0.5,
    last_accessed_days_ago: float = 0,
    pinned: bool = False,
    is_consolidated: bool = False,
) -> MemoryEntry:
    now = datetime.utcnow()
    last_accessed = now - timedelta(days=last_accessed_days_ago)
    return MemoryEntry(
        agent_id=uuid4(),
        type=type,
        content="test memory",
        importance=importance,
        pinned=pinned,
        is_consolidated=is_consolidated,
        last_accessed_at=last_accessed,
        created_at=last_accessed,
    )


# ------------------------------------------------------------------
# effective_importance
# ------------------------------------------------------------------

def test_fresh_entry_keeps_full_importance():
    entry = make_entry(importance=0.8, last_accessed_days_ago=0)
    score = effective_importance(entry)
    assert abs(score - 0.8) < 0.01


def test_old_episodic_decays_significantly():
    """After 2 half-lives the score should be ~25% of original."""
    profile = DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC]
    half_life = profile.half_life_days
    entry = make_entry(
        type=MemoryType.EPISODIC,
        importance=0.8,
        last_accessed_days_ago=half_life * 2,
    )
    score = effective_importance(entry, profile)
    # 0.8 * 0.5^2 = 0.2, clamped to min_importance if needed
    assert score <= 0.22


def test_semantic_decays_slower_than_episodic():
    """Semantic memory should retain more value over the same time period."""
    days = 30
    episodic_entry = make_entry(
        type=MemoryType.EPISODIC, importance=0.8, last_accessed_days_ago=days
    )
    semantic_entry = make_entry(
        type=MemoryType.SEMANTIC, importance=0.8, last_accessed_days_ago=days
    )
    episodic_score = effective_importance(
        episodic_entry, DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC]
    )
    semantic_score = effective_importance(
        semantic_entry, DEFAULT_DECAY_PROFILES[MemoryType.SEMANTIC]
    )
    assert semantic_score > episodic_score


def test_pinned_entry_never_decays():
    entry = make_entry(importance=0.7, last_accessed_days_ago=9999, pinned=True)
    score = effective_importance(entry)
    assert score == 0.7


def test_no_decay_strategy_returns_base_importance():
    profile = DecayProfile(strategy=DecayStrategy.NONE, half_life_days=0)
    entry = make_entry(importance=0.6, last_accessed_days_ago=365)
    score = effective_importance(entry, profile)
    assert score == 0.6


def test_score_never_falls_below_min_importance():
    profile = DecayProfile(
        strategy=DecayStrategy.EXPONENTIAL,
        half_life_days=1.0,
        min_importance=0.15,
    )
    entry = make_entry(importance=0.9, last_accessed_days_ago=365)
    score = effective_importance(entry, profile)
    assert score >= 0.15


def test_consolidated_entry_decays_slower():
    """Consolidated summaries should outrank originals of the same age."""
    days = 20
    original = make_entry(importance=0.5, last_accessed_days_ago=days)
    consolidated = make_entry(
        importance=0.5, last_accessed_days_ago=days, is_consolidated=True
    )
    original_score = effective_importance(original)
    consolidated_score = effective_importance(consolidated)
    assert consolidated_score > original_score


def test_linear_decay():
    profile = DecayProfile(
        strategy=DecayStrategy.LINEAR,
        half_life_days=50.0,
        min_importance=0.0,
    )
    # At exactly 2 * half_life, linear decay should reach 0 (before floor)
    entry = make_entry(importance=1.0, last_accessed_days_ago=100)
    score = effective_importance(entry, profile)
    assert score == 0.0 or score == profile.min_importance


# ------------------------------------------------------------------
# should_consolidate
# ------------------------------------------------------------------

def test_fresh_entry_not_a_consolidation_candidate():
    entry = make_entry(last_accessed_days_ago=0)
    assert should_consolidate(entry) is False


def test_very_old_episodic_is_consolidation_candidate():
    profile = DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC]
    # After many half-lives it should drop below consolidation_threshold
    entry = make_entry(
        type=MemoryType.EPISODIC,
        importance=0.5,
        last_accessed_days_ago=profile.half_life_days * 10,
    )
    assert should_consolidate(entry, profile) is True


def test_pinned_entry_not_a_consolidation_candidate():
    entry = make_entry(last_accessed_days_ago=999, pinned=True)
    assert should_consolidate(entry) is False


def test_consolidated_entry_not_re_consolidated():
    entry = make_entry(last_accessed_days_ago=999, is_consolidated=True)
    assert should_consolidate(entry) is False


# ------------------------------------------------------------------
# decay_sorted
# ------------------------------------------------------------------

def test_decay_sorted_ranks_fresh_above_stale():
    fresh = make_entry(importance=0.5, last_accessed_days_ago=0)
    stale = make_entry(importance=0.5, last_accessed_days_ago=365)
    sorted_entries = decay_sorted([stale, fresh])
    assert sorted_entries[0] is fresh


def test_decay_sorted_ranks_high_importance_above_low():
    high = make_entry(importance=0.9, last_accessed_days_ago=0)
    low = make_entry(importance=0.2, last_accessed_days_ago=0)
    sorted_entries = decay_sorted([low, high])
    assert sorted_entries[0] is high


def test_consolidation_candidates_returns_only_decayed():
    fresh = make_entry(importance=0.8, last_accessed_days_ago=0)
    profile = DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC]
    stale = make_entry(
        type=MemoryType.EPISODIC,
        importance=0.5,
        last_accessed_days_ago=profile.half_life_days * 10,
    )
    candidates = consolidation_candidates([fresh, stale])
    assert stale in candidates
    assert fresh not in candidates
