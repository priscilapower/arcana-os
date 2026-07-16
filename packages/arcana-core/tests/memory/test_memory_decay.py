"""Unit tests for retrieval-time memory decay.

Decay is a *score, not a delete*: the stored ``importance`` is never mutated,
only the effective importance used for ranking changes with age. Time is passed
in explicitly (``now``) so every case is deterministic — no sleeps.
"""

from datetime import UTC, datetime, timedelta

import pytest

from arcana.memory.decay import (
    decay_sorted,
    effective_importance,
    resolve_decay_profiles,
    should_consolidate,
)
from arcana.types import (
    DEFAULT_DECAY_PROFILES,
    CardDecayConfig,
    DecayProfile,
    DecayStrategy,
    MemoryType,
)
from tests.support.factories import make_entry

# A fixed "now" so age is a pure function of the entry's timestamps.
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _entry(**overrides: object):
    """A memory entry whose decay clock is fully controlled by the test.

    ``created_at``/``last_accessed_at`` default to ``NOW`` (age zero) unless a
    test seeds them into the past.
    """
    return make_entry(**{"created_at": NOW, "last_accessed_at": NOW, **overrides})


# ---------------------------------------------------------------------------
# effective_importance — exponential curve + floor
# ---------------------------------------------------------------------------


def test_no_decay_at_age_zero():
    """A fresh entry keeps its full stored importance."""
    profile = DecayProfile(half_life_days=14.0, min_importance=0.05)
    entry = _entry(importance=0.8, type=MemoryType.EPISODIC)
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.8)


def test_halves_at_one_half_life():
    """Importance halves after exactly one half-life."""
    profile = DecayProfile(half_life_days=14.0, min_importance=0.0)
    entry = _entry(importance=0.8, last_accessed_at=NOW - timedelta(days=14))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.4)


def test_quarters_at_two_half_lives():
    profile = DecayProfile(half_life_days=10.0, min_importance=0.0)
    entry = _entry(importance=1.0, last_accessed_at=NOW - timedelta(days=20))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.25)


def test_min_importance_floor_clamps_deep_decay():
    """A long-aged entry never decays below its floor."""
    profile = DecayProfile(half_life_days=14.0, min_importance=0.1)
    entry = _entry(importance=0.9, last_accessed_at=NOW - timedelta(days=1000))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.1)


def test_future_reference_clamps_age_to_zero():
    """A reference timestamp in the future does not produce negative decay."""
    profile = DecayProfile(half_life_days=14.0)
    entry = _entry(importance=0.5, last_accessed_at=NOW + timedelta(days=5))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.5)


def test_linear_strategy_reaches_half_at_half_life():
    profile = DecayProfile(strategy=DecayStrategy.LINEAR, half_life_days=10.0, min_importance=0.0)
    entry = _entry(importance=1.0, last_accessed_at=NOW - timedelta(days=10))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# NONE / pinned exempt
# ---------------------------------------------------------------------------


def test_strategy_none_never_decays():
    profile = DecayProfile(strategy=DecayStrategy.NONE, half_life_days=0.0, min_importance=1.0)
    entry = _entry(importance=0.7, last_accessed_at=NOW - timedelta(days=10_000))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.7)


def test_pinned_entry_never_decays():
    """A pin exempts an entry from decay even under an aggressive profile."""
    profile = DecayProfile(half_life_days=1.0, min_importance=0.0)
    entry = _entry(importance=0.6, pinned=True, last_accessed_at=NOW - timedelta(days=365))
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.6)


def test_pinned_entry_never_consolidates():
    profile = DecayProfile(half_life_days=1.0, min_importance=0.0, consolidation_threshold=0.5)
    entry = _entry(importance=0.6, pinned=True, last_accessed_at=NOW - timedelta(days=365))
    assert should_consolidate(entry, profile, NOW) is False


def test_none_profile_never_consolidates():
    profile = DecayProfile(strategy=DecayStrategy.NONE, half_life_days=0.0, min_importance=0.0)
    entry = _entry(importance=0.01, last_accessed_at=NOW - timedelta(days=10_000))
    assert should_consolidate(entry, profile, NOW) is False


# ---------------------------------------------------------------------------
# refresh_on_access resets the decay clock
# ---------------------------------------------------------------------------


def test_refresh_on_access_measures_from_last_access():
    """With refresh_on_access, a recent access resets the clock despite an old birth."""
    profile = DecayProfile(half_life_days=14.0, min_importance=0.0, refresh_on_access=True)
    entry = _entry(
        importance=0.8,
        created_at=NOW - timedelta(days=200),
        last_accessed_at=NOW,  # touched just now
    )
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.8)


def test_without_refresh_measures_from_creation():
    """When refresh_on_access is off, age is measured from creation, not access."""
    profile = DecayProfile(half_life_days=14.0, min_importance=0.0, refresh_on_access=False)
    entry = _entry(
        importance=0.8,
        created_at=NOW - timedelta(days=14),
        last_accessed_at=NOW,  # ignored when refresh is off
    )
    assert effective_importance(entry, profile, NOW) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# should_consolidate threshold
# ---------------------------------------------------------------------------


def test_should_consolidate_when_below_threshold():
    profile = DecayProfile(half_life_days=14.0, min_importance=0.0, consolidation_threshold=0.25)
    aged = _entry(importance=0.4, last_accessed_at=NOW - timedelta(days=60))
    fresh = _entry(importance=0.4, last_accessed_at=NOW)
    assert should_consolidate(aged, profile, NOW) is True
    assert should_consolidate(fresh, profile, NOW) is False


def test_floor_at_or_above_threshold_never_ages_out():
    """A type whose floor sits above its threshold (e.g. SEMANTIC) persists."""
    profile = DEFAULT_DECAY_PROFILES[MemoryType.SEMANTIC]  # min 0.2 > threshold 0.1
    entry = _entry(importance=0.5, type=MemoryType.SEMANTIC, last_accessed_at=NOW - timedelta(days=10_000))
    assert should_consolidate(entry, profile, NOW) is False


# ---------------------------------------------------------------------------
# decay_sorted — typed half-lives rank correctly & age-out drops
# ---------------------------------------------------------------------------


def test_decay_sorted_ages_out_episodic_keeps_semantic():
    """Old episodic (14d HL) ages out; old semantic (180d HL) survives — typed decay."""
    old = NOW - timedelta(days=120)
    episodic = _entry(importance=0.4, type=MemoryType.EPISODIC, content="ep", created_at=old, last_accessed_at=old)
    semantic = _entry(importance=0.5, type=MemoryType.SEMANTIC, content="sem", created_at=old, last_accessed_at=old)
    ranked = decay_sorted([episodic, semantic], DEFAULT_DECAY_PROFILES, NOW)
    assert [e.content for e in ranked] == ["sem"]


def test_decay_sorted_orders_by_effective_importance():
    """A fresher entry outranks a more-decayed one of equal stored importance."""
    fresh = _entry(importance=0.5, type=MemoryType.SEMANTIC, content="fresh", last_accessed_at=NOW)
    stale = _entry(
        importance=0.5,
        type=MemoryType.SEMANTIC,
        content="stale",
        last_accessed_at=NOW - timedelta(days=120),
    )
    ranked = decay_sorted([stale, fresh], DEFAULT_DECAY_PROFILES, NOW)
    assert [e.content for e in ranked] == ["fresh", "stale"]


def test_decay_sorted_pinned_sorts_first_and_survives():
    """A pinned, fully-decayed entry is exempt and still sorts first."""
    old = NOW - timedelta(days=1000)
    pinned = _entry(importance=0.05, type=MemoryType.EPISODIC, content="pin", pinned=True, last_accessed_at=old)
    fresh = _entry(importance=0.9, type=MemoryType.SEMANTIC, content="fresh", last_accessed_at=NOW)
    ranked = decay_sorted([fresh, pinned], DEFAULT_DECAY_PROFILES, NOW)
    assert ranked[0].content == "pin"
    assert {e.content for e in ranked} == {"pin", "fresh"}


def test_decay_sorted_missing_profile_ranks_on_stored_importance():
    """A type with no profile is treated as non-decaying rather than dropped."""
    old = NOW - timedelta(days=1000)
    a = _entry(importance=0.3, type=MemoryType.EPISODIC, content="a", last_accessed_at=old)
    b = _entry(importance=0.7, type=MemoryType.EPISODIC, content="b", last_accessed_at=old)
    ranked = decay_sorted([a, b], {}, NOW)  # empty profile map
    assert [e.content for e in ranked] == ["b", "a"]


# ---------------------------------------------------------------------------
# resolve_decay_profiles — card-driven & blended half-lives
# ---------------------------------------------------------------------------


def test_resolve_uses_card_half_lives_and_default_fallback():
    """Set card half-lives override defaults; unset (None) fields keep the default."""
    config = CardDecayConfig(episodic_half_life_days=3.0)  # only episodic set
    profiles = resolve_decay_profiles(config)
    assert profiles[MemoryType.EPISODIC].half_life_days == 3.0
    # Unset types fall back to the system default half-life.
    assert profiles[MemoryType.SEMANTIC].half_life_days == DEFAULT_DECAY_PROFILES[MemoryType.SEMANTIC].half_life_days
    # Non-half-life fields (floor, threshold) are inherited from the default.
    assert profiles[MemoryType.EPISODIC].min_importance == DEFAULT_DECAY_PROFILES[MemoryType.EPISODIC].min_importance


def test_resolve_world_never_forgets():
    """The World gets DecayStrategy.NONE on every type regardless of card half-lives."""
    profiles = resolve_decay_profiles(CardDecayConfig(episodic_half_life_days=3.0), world=True)
    assert all(p.strategy is DecayStrategy.NONE for p in profiles.values())


def test_card_blended_half_lives_rank_via_engine():
    """A card's blended half-lives flow through resolve into a working profile map.

    The Fool (episodic HL 3d) blended with a modifier yields a short episodic
    half-life, so a two-week-old episodic entry ages out under the blend while a
    semantic one persists — end-to-end from CardEngine to decay.
    """
    from arcana.cards.engine import CardEngine
    from arcana.cards.registry import get_registry
    from arcana.types import Card

    config = CardEngine(get_registry()).resolve(Card.FOOL, [Card.HIGH_PRIESTESS])
    profiles = resolve_decay_profiles(config.decay_config)

    # Blended episodic half-life is 3d×0.7 + 180d×0.3 ≈ 56d (70/30 toward the Fool),
    # while blended semantic is far longer — so a four-month-old episodic entry ages
    # out under the blend while the semantic one persists.
    old = NOW - timedelta(days=120)
    episodic = _entry(importance=0.4, type=MemoryType.EPISODIC, content="ep", created_at=old, last_accessed_at=old)
    semantic = _entry(importance=0.5, type=MemoryType.SEMANTIC, content="sem", created_at=old, last_accessed_at=old)
    ranked = decay_sorted([episodic, semantic], profiles, NOW)
    assert "ep" not in [e.content for e in ranked]
    assert "sem" in [e.content for e in ranked]
