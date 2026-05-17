"""Tests for memory quality features: confidence, conflicts, whiteboard."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from arcana.types.memory import (
    ConfidenceSource,
    ConflictStatus,
    MemoryConflict,
    MemoryEntry,
    MemoryMetrics,
    MemoryProfile,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    MemoryWhiteboard,
    WhiteboardStatus,
)
from arcana.world.engine import World


# ---------------------------------------------------------------------------
# MemoryEntry — confidence
# ---------------------------------------------------------------------------

def test_entry_default_confidence_is_full():
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="The capital of France is Paris",
    )
    assert entry.confidence == 1.0
    assert entry.confidence_source == ConfidenceSource.AGENT


def test_entry_low_confidence_flags_hallucination_risk():
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="User said their name is probably Alex or maybe Sam",
        confidence=0.3,
    )
    assert entry.confidence < 0.5


def test_entry_user_confirmed_has_highest_confidence():
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.PREFERENCE,
        content="User prefers dark mode",
        confidence=1.0,
        confidence_source=ConfidenceSource.USER_CONFIRMED,
    )
    assert entry.confidence_source == ConfidenceSource.USER_CONFIRMED


def test_consolidated_entry_has_correct_source():
    original_ids = [uuid4(), uuid4(), uuid4()]
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="User has asked about RAG multiple times; interested in retrieval systems",
        confidence=0.9,
        confidence_source=ConfidenceSource.CONSOLIDATED,
        is_consolidated=True,
        consolidated_from=original_ids,
    )
    assert entry.is_consolidated is True
    assert len(entry.consolidated_from) == 3
    assert entry.confidence_source == ConfidenceSource.CONSOLIDATED


def test_should_promote_to_global():
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="Critical system fact",
        importance=0.95,
        scope=MemoryScope.PRIVATE,
    )
    assert entry.should_promote_to_global is True


def test_should_not_promote_low_importance():
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.EPISODIC,
        content="User asked about the weather",
        importance=0.4,
    )
    assert entry.should_promote_to_global is False


# ---------------------------------------------------------------------------
# MemoryConflict
# ---------------------------------------------------------------------------

def test_conflict_starts_open():
    conflict = MemoryConflict(
        pool_name="project-arcana",
        entry_a_id=uuid4(),
        entry_b_id=uuid4(),
        similarity_score=0.92,
    )
    assert conflict.status == ConflictStatus.OPEN
    assert conflict.resolved_entry_id is None


def test_conflict_resolution():
    winner_id = uuid4()
    conflict = MemoryConflict(
        pool_name="project-arcana",
        entry_a_id=winner_id,
        entry_b_id=uuid4(),
        similarity_score=0.88,
        status=ConflictStatus.RESOLVED,
        resolved_entry_id=winner_id,
        resolution_note="Entry A has higher confidence (1.0 vs 0.6)",
        resolved_at=datetime.utcnow(),
    )
    assert conflict.status == ConflictStatus.RESOLVED
    assert conflict.resolved_entry_id == winner_id


# ---------------------------------------------------------------------------
# MemoryWhiteboard
# ---------------------------------------------------------------------------

def test_whiteboard_starts_active():
    wb = MemoryWhiteboard(
        name="morning-brief-2026-05-18",
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    assert wb.status == WhiteboardStatus.ACTIVE
    assert wb.closed_at is None
    assert wb.promoted_entry_ids == []


def test_whiteboard_linked_to_automation_run():
    run_id = uuid4()
    spread_id = uuid4()
    agent_ids = [uuid4(), uuid4(), uuid4()]

    wb = MemoryWhiteboard(
        name="research-draft-review",
        automation_run_id=run_id,
        spread_id=spread_id,
        participating_agent_ids=agent_ids,
        expires_at=datetime.utcnow() + timedelta(hours=4),
        promote_to_pool="project-arcana",
    )
    assert wb.automation_run_id == run_id
    assert wb.spread_id == spread_id
    assert len(wb.participating_agent_ids) == 3
    assert wb.promote_to_pool == "project-arcana"


# ---------------------------------------------------------------------------
# MemoryProfile — quality thresholds
# ---------------------------------------------------------------------------

def test_memory_profile_confidence_thresholds():
    profile = MemoryProfile(
        agent_id=uuid4(),
        min_confidence_to_store=0.4,
        min_confidence_for_context=0.6,
    )
    assert profile.min_confidence_to_store == 0.4
    assert profile.min_confidence_for_context == 0.6


def test_memory_profile_defaults_are_sensible():
    profile = MemoryProfile(agent_id=uuid4())
    # Should reject very low confidence entries
    assert profile.min_confidence_to_store > 0.0
    # Should require reasonable confidence for context injection
    assert profile.min_confidence_for_context >= profile.min_confidence_to_store


# ---------------------------------------------------------------------------
# MemoryMetrics
# ---------------------------------------------------------------------------

def test_metrics_structure():
    metrics = MemoryMetrics(
        agent_id=uuid4(),
        agent_name="researcher",
        period_days=7,
        total_entries=150,
        entries_by_type={"episodic": 80, "semantic": 50, "procedural": 20},
        entries_below_consolidation_threshold=12,
        conflict_count=2,
        avg_effective_importance_at_retrieval=0.62,
        retrieval_hit_rate=0.85,
        avg_confidence_at_retrieval=0.91,
        avg_memory_context_tokens=340,
    )
    assert metrics.total_entries == 150
    assert metrics.conflict_count == 2
    assert metrics.retrieval_hit_rate == 0.85
    assert metrics.avg_confidence_at_retrieval == 0.91


def test_metrics_low_retrieval_hit_rate_signal():
    """Low hit rate means memory isn't helping — context being re-explained."""
    metrics = MemoryMetrics(
        agent_id=uuid4(),
        agent_name="test",
        period_days=7,
        retrieval_hit_rate=0.2,  # very low — memory not contributing
    )
    assert metrics.retrieval_hit_rate < 0.5


# ---------------------------------------------------------------------------
# WorldEngine — whiteboard lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_world_creates_whiteboard():
    world = World()
    run_id = uuid4()
    wb = await world.create_whiteboard(
        name="test-run",
        automation_run_id=run_id,
        ttl_hours=2.0,
    )
    assert wb.status == WhiteboardStatus.ACTIVE
    assert wb.automation_run_id == run_id
    assert world.get_whiteboard(wb.id) is wb


@pytest.mark.asyncio
async def test_world_closes_whiteboard():
    world = World()
    wb = await world.create_whiteboard(name="test-run", ttl_hours=1.0)
    promoted = [uuid4(), uuid4()]
    closed = await world.close_whiteboard(wb.id, promote_entry_ids=promoted)
    assert closed is not None
    assert closed.status == WhiteboardStatus.ARCHIVED
    assert closed.promoted_entry_ids == promoted
    assert closed.closed_at is not None


@pytest.mark.asyncio
async def test_world_expires_stale_whiteboards():
    world = World()
    # Create a whiteboard with a past expiry
    wb = await world.create_whiteboard(name="expired-run", ttl_hours=0.0)
    wb.expires_at = datetime.utcnow() - timedelta(hours=1)

    expired_count = await world.expire_whiteboards()
    assert expired_count >= 1
    assert wb.status == WhiteboardStatus.EXPIRED


@pytest.mark.asyncio
async def test_world_metrics_returns_stub():
    world = World()
    agent_id = uuid4()
    metrics = await world.memory_metrics(agent_id, period_days=7)
    assert metrics.agent_id == agent_id
    assert metrics.period_days == 7
