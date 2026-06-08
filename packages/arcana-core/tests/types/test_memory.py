from datetime import UTC, datetime
from uuid import UUID, uuid4

from arcana.types.memory import (
    AdapterCapabilities,
    AdapterHealth,
    ConfidenceSource,
    ConflictStatus,
    DecayProfile,
    DecayStrategy,
    MemoryConflict,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    MemoryWhiteboard,
    RetrievalMode,
)

# ---------------------------------------------------------------------------
# MemoryEntry — defaults
# ---------------------------------------------------------------------------


def _make_entry(**kwargs) -> MemoryEntry:
    defaults = dict(
        agent_id=uuid4(),
        type=MemoryType.EPISODIC,
        content="The API rate limit is 1000 req/min.",
    )
    return MemoryEntry(**{**defaults, **kwargs})


def test_memory_entry_defaults():
    entry = _make_entry()
    assert entry.scope == MemoryScope.PRIVATE
    assert entry.importance == 0.5
    assert entry.confidence == 1.0
    assert entry.confidence_source == ConfidenceSource.AGENT
    assert entry.pinned is False
    assert entry.is_consolidated is False
    assert entry.has_conflict is False
    assert entry.conflict_id is None
    assert entry.embedding is None
    assert entry.pool_name is None
    assert entry.access_count == 0
    assert entry.consolidated_from == []
    assert entry.tags == []


def test_memory_entry_id_is_uuid():
    entry = _make_entry()
    assert isinstance(entry.id, UUID)


def test_memory_entry_should_promote_true():
    entry = _make_entry(importance=0.9, scope=MemoryScope.PRIVATE)
    assert entry.should_promote_to_global is True


def test_memory_entry_should_promote_true_above_threshold():
    entry = _make_entry(importance=0.95, scope=MemoryScope.PRIVATE)
    assert entry.should_promote_to_global is True


def test_memory_entry_should_not_promote_low_importance():
    entry = _make_entry(importance=0.89, scope=MemoryScope.PRIVATE)
    assert entry.should_promote_to_global is False


def test_memory_entry_should_not_promote_wrong_scope():
    entry = _make_entry(importance=0.95, scope=MemoryScope.SHARED)
    assert entry.should_promote_to_global is False


def test_memory_entry_should_not_promote_global_scope():
    entry = _make_entry(importance=0.95, scope=MemoryScope.GLOBAL)
    assert entry.should_promote_to_global is False


def test_memory_entry_bump_access():
    entry = _make_entry()
    original_time = entry.last_accessed_at
    assert entry.access_count == 0

    entry.bump_access()

    assert entry.access_count == 1
    assert entry.last_accessed_at >= original_time


def test_memory_entry_bump_access_increments_repeatedly():
    entry = _make_entry()
    entry.bump_access()
    entry.bump_access()
    entry.bump_access()
    assert entry.access_count == 3


# ---------------------------------------------------------------------------
# MemoryQuery — defaults
# ---------------------------------------------------------------------------


def test_memory_query_defaults():
    q = MemoryQuery()
    assert q.limit == 10
    assert q.retrieval_mode == RetrievalMode.semantic
    assert q.include_archived is False
    assert q.include_conflicted is False
    assert q.min_importance == 0.0
    assert q.min_confidence == 0.0
    assert q.keywords == []
    assert q.tags == []
    assert q.agent_id is None
    assert q.text is None
    assert q.type is None
    assert q.scope is None
    assert q.pool_name is None


def test_memory_query_with_filters():
    agent_id = uuid4()
    q = MemoryQuery(
        agent_id=agent_id,
        text="rate limit",
        type=MemoryType.SEMANTIC,
        scope=MemoryScope.SHARED,
        limit=20,
        min_importance=0.3,
    )
    assert q.agent_id == agent_id
    assert q.text == "rate limit"
    assert q.type == MemoryType.SEMANTIC
    assert q.limit == 20


# ---------------------------------------------------------------------------
# DecayProfile
# ---------------------------------------------------------------------------


def test_decay_profile_defaults():
    profile = DecayProfile()
    assert profile.strategy == DecayStrategy.EXPONENTIAL
    assert profile.half_life_days == 30.0
    assert profile.min_importance == 0.1
    assert profile.refresh_on_access is True
    assert profile.consolidation_threshold == 0.2


def test_decay_profile_none_strategy():
    profile = DecayProfile(strategy=DecayStrategy.NONE, half_life_days=0, min_importance=1.0)
    assert profile.strategy == DecayStrategy.NONE


# ---------------------------------------------------------------------------
# MemoryConflict
# ---------------------------------------------------------------------------


def test_memory_conflict_defaults():
    conflict = MemoryConflict(
        pool_name="project-alpha",
        entry_a_id=uuid4(),
        entry_b_id=uuid4(),
        similarity_score=0.92,
    )
    assert conflict.status == ConflictStatus.OPEN
    assert conflict.resolved_entry_id is None
    assert conflict.resolution_note == ""
    assert isinstance(conflict.id, UUID)


# ---------------------------------------------------------------------------
# MemoryWhiteboard
# ---------------------------------------------------------------------------


def test_memory_whiteboard_defaults():
    expires = datetime(2026, 12, 31, tzinfo=UTC)
    wb = MemoryWhiteboard(name="research-run-001", expires_at=expires)
    assert wb.status.value == "active"
    assert wb.participating_agent_ids == []
    assert wb.promoted_entry_ids == []
    assert wb.archived_entry_ids == []
    assert wb.automation_run_id is None
    assert wb.spread_id is None
    assert wb.promote_to_pool is None
    assert wb.closed_at is None


# ---------------------------------------------------------------------------
# AdapterCapabilities / AdapterHealth
# ---------------------------------------------------------------------------


def test_adapter_capabilities_defaults():
    caps = AdapterCapabilities()
    assert caps.supports_vector is False
    assert caps.supports_full_text is False
    assert caps.supports_tags is True
    assert caps.supports_time_range is True
    assert caps.is_writable is True
    assert caps.is_persistent is True


def test_adapter_capabilities_vector_store():
    caps = AdapterCapabilities(supports_vector=True, supports_full_text=True)
    assert caps.supports_vector is True
    assert caps.supports_full_text is True


def test_adapter_health_healthy():
    health = AdapterHealth(adapter_id="sqlite-private", healthy=True)
    assert health.healthy is True
    assert health.message == ""


def test_adapter_health_unhealthy_with_message():
    health = AdapterHealth(adapter_id="sqlite-private", healthy=False, message="Connection refused")
    assert health.healthy is False
    assert "refused" in health.message
