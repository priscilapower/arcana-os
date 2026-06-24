"""Unit tests for SQLiteAdapter. No LLM calls."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import MemoryStorageError, SQLiteAdapter
from arcana.memory.migrations import latest_version, migrate_to_latest
from arcana.types import (
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
)
from arcana.types.memory import ConfidenceSource, RetrievalMode

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
async def adapter(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "memory.db")
    await a.connect()
    yield a
    await a.aclose()


def _entry(**overrides) -> MemoryEntry:
    base = dict(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="the sky is blue",
        importance=0.5,
    )
    base.update(overrides)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Round-trip & upsert
# --------------------------------------------------------------------------


async def test_write_read_round_trip_all_fields(adapter: SQLiteAdapter):
    agent = uuid4()
    src = uuid4()
    conflict = uuid4()
    consolidated = [uuid4(), uuid4()]
    entry = _entry(
        agent_id=agent,
        type=MemoryType.PROCEDURAL,
        content="how to brew coffee",
        scope=MemoryScope.SHARED,
        pool_name="kitchen",
        importance=0.7,
        pinned=True,
        is_consolidated=True,
        consolidated_from=consolidated,
        confidence=0.8,
        confidence_source=ConfidenceSource.USER_CONFIRMED,
        has_conflict=True,
        conflict_id=conflict,
        embedding=[0.1, 0.2, 0.3],
        source_session_id=src,
        tags=["coffee", "morning"],
        access_count=3,
    )
    await adapter.write(entry)

    # include_conflicted because this entry is flagged
    got = await adapter.search(MemoryQuery(agent_id=agent, include_conflicted=True))
    assert len(got) == 1
    r = got[0]
    assert r.id == entry.id
    assert r.type == MemoryType.PROCEDURAL
    assert r.scope == MemoryScope.SHARED
    assert r.pool_name == "kitchen"
    assert r.pinned is True
    assert r.is_consolidated is True
    assert r.consolidated_from == consolidated
    assert r.confidence == 0.8
    assert r.confidence_source == ConfidenceSource.USER_CONFIRMED
    assert r.has_conflict is True
    assert r.conflict_id == conflict
    assert r.embedding == [0.1, 0.2, 0.3]
    assert r.source_session_id == src
    assert r.tags == ["coffee", "morning"]


async def test_write_is_upsert(adapter: SQLiteAdapter):
    agent = uuid4()
    entry = _entry(agent_id=agent, content="v1")
    await adapter.write(entry)
    entry.content = "v2"
    await adapter.write(entry)

    got = await adapter.search(MemoryQuery(agent_id=agent))
    assert len(got) == 1
    assert got[0].content == "v2"


async def test_empty_db_returns_empty(adapter: SQLiteAdapter):
    assert await adapter.search(MemoryQuery()) == []


# --------------------------------------------------------------------------
# Scope, filtering, ordering
# --------------------------------------------------------------------------


async def test_scope_filtering(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, scope=MemoryScope.PRIVATE))
    await adapter.write(_entry(agent_id=agent, scope=MemoryScope.SHARED, pool_name="p"))
    private = await adapter.search(MemoryQuery(agent_id=agent, scope=MemoryScope.PRIVATE))
    assert len(private) == 1
    assert private[0].scope == MemoryScope.PRIVATE


async def test_filters_importance_confidence_type_pool_time(adapter: SQLiteAdapter):
    agent = uuid4()
    old = datetime.now(UTC) - timedelta(days=10)
    await adapter.write(_entry(agent_id=agent, importance=0.2, confidence=0.4))
    await adapter.write(
        _entry(
            agent_id=agent,
            importance=0.9,
            confidence=0.95,
            type=MemoryType.EPISODIC,
            scope=MemoryScope.SHARED,
            pool_name="pool-a",
            created_at=old,
        )
    )

    assert len(await adapter.search(MemoryQuery(agent_id=agent, min_importance=0.5))) == 1
    assert len(await adapter.search(MemoryQuery(agent_id=agent, min_confidence=0.9))) == 1
    assert len(await adapter.search(MemoryQuery(agent_id=agent, type=MemoryType.EPISODIC))) == 1
    assert len(await adapter.search(MemoryQuery(agent_id=agent, pool_name="pool-a"))) == 1
    recent_only = await adapter.search(MemoryQuery(agent_id=agent, time_from=datetime.now(UTC) - timedelta(days=1)))
    assert len(recent_only) == 1
    assert recent_only[0].importance == 0.2


async def test_conflicted_excluded_by_default(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, has_conflict=True))
    await adapter.write(_entry(agent_id=agent, has_conflict=False))
    assert len(await adapter.search(MemoryQuery(agent_id=agent))) == 1
    assert len(await adapter.search(MemoryQuery(agent_id=agent, include_conflicted=True))) == 2


async def test_keyword_and_tag_filtering(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="espresso recipe", tags=["coffee"]))
    await adapter.write(_entry(agent_id=agent, content="tea recipe", tags=["tea"]))

    kw = await adapter.search(MemoryQuery(agent_id=agent, keywords=["espresso"]))
    assert len(kw) == 1 and "espresso" in kw[0].content

    tagged = await adapter.search(MemoryQuery(agent_id=agent, tags=["tea"]))
    assert len(tagged) == 1 and tagged[0].tags == ["tea"]


async def test_ordering_pinned_then_importance(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, importance=0.9, content="high"))
    await adapter.write(_entry(agent_id=agent, importance=0.1, content="low"))
    await adapter.write(_entry(agent_id=agent, importance=0.1, content="pinned", pinned=True))

    got = await adapter.search(MemoryQuery(agent_id=agent))
    assert got[0].content == "pinned"  # pinned wins regardless of importance
    assert got[1].content == "high"


async def test_limit(adapter: SQLiteAdapter):
    agent = uuid4()
    for i in range(5):
        await adapter.write(_entry(agent_id=agent, content=f"e{i}"))
    assert len(await adapter.search(MemoryQuery(agent_id=agent, limit=3))) == 3


async def test_retrieval_mode_hybrid_runs_keyword_leg(adapter: SQLiteAdapter):
    # A hybrid text query runs FTS5 BM25 keyword matching: matching text returns
    # the row, non-matching text returns nothing. Never errors.
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="the sky is blue"))
    hit = await adapter.search(MemoryQuery(agent_id=agent, text="sky", retrieval_mode=RetrievalMode.hybrid))
    assert len(hit) == 1
    miss = await adapter.search(MemoryQuery(agent_id=agent, text="zebra", retrieval_mode=RetrievalMode.hybrid))
    assert miss == []


# --------------------------------------------------------------------------
# Access tracking
# --------------------------------------------------------------------------


async def test_access_tracking_increments(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, access_count=0))
    first = await adapter.search(MemoryQuery(agent_id=agent))
    assert first[0].access_count == 1
    second = await adapter.search(MemoryQuery(agent_id=agent))
    assert second[0].access_count == 2


async def test_access_tracking_can_be_disabled(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "m.db", refresh_on_access=False)
    await a.connect()
    agent = uuid4()
    await a.write(_entry(agent_id=agent, access_count=0))
    got = await a.search(MemoryQuery(agent_id=agent))
    assert got[0].access_count == 0
    await a.aclose()


# --------------------------------------------------------------------------
# Importance-based promotion to GLOBAL
# --------------------------------------------------------------------------


async def test_high_importance_private_promotes_to_global(tmp_path: Path):
    global_store = SQLiteAdapter(tmp_path / "global.db")
    await global_store.connect()
    agent_store = SQLiteAdapter(tmp_path / "agent.db", global_store=global_store)
    await agent_store.connect()

    agent = uuid4()
    entry = _entry(agent_id=agent, importance=0.95, scope=MemoryScope.PRIVATE)
    await agent_store.write(entry)

    promoted = await global_store.search(MemoryQuery(scope=MemoryScope.GLOBAL))
    assert len(promoted) == 1
    assert promoted[0].id == entry.id
    assert promoted[0].scope == MemoryScope.GLOBAL
    # the promoted copy must not itself re-promote (no infinite loop)
    assert promoted[0].should_promote_to_global is False

    await agent_store.aclose()
    await global_store.aclose()


async def test_low_importance_does_not_promote(tmp_path: Path):
    global_store = SQLiteAdapter(tmp_path / "global.db")
    await global_store.connect()
    agent_store = SQLiteAdapter(tmp_path / "agent.db", global_store=global_store)
    await agent_store.connect()

    await agent_store.write(_entry(importance=0.5, scope=MemoryScope.PRIVATE))
    assert await global_store.search(MemoryQuery(scope=MemoryScope.GLOBAL)) == []

    await agent_store.aclose()
    await global_store.aclose()


async def test_promotion_noop_without_global_store(adapter: SQLiteAdapter):
    # High importance + no global store configured = succeeds, no error.
    await adapter.write(_entry(importance=0.99, scope=MemoryScope.PRIVATE))


# --------------------------------------------------------------------------
# Lifecycle, migrations, protocol conformance
# --------------------------------------------------------------------------


async def test_runtime_protocol_conformance(adapter: SQLiteAdapter):
    assert isinstance(adapter, MemoryAdapter)


async def test_lazy_connect_and_double_close(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "m.db")
    # no explicit connect(): write should lazily connect
    await a.write(_entry())
    await a.aclose()
    await a.aclose()  # idempotent


async def test_for_agent_path(tmp_path: Path):
    agent_id = uuid4()
    a = SQLiteAdapter.for_agent(agent_id, base_dir=tmp_path)
    await a.connect()
    assert (tmp_path / str(agent_id) / "memory.db").exists()
    await a.aclose()


async def test_fresh_db_migrates_to_latest(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "m.db")
    await a.connect()
    conn = a._conn
    assert conn is not None
    version = (await (await conn.execute("PRAGMA user_version")).fetchone())[0]
    assert version == latest_version()
    tables = await (await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    assert any(row[0] == "memory_entries" for row in tables)
    await a.aclose()


async def test_reconnect_is_idempotent(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "m.db")
    await a.connect()
    await a.connect()  # second call: already at head, no error
    await a.aclose()


async def test_migration_rollback_keeps_version(tmp_path: Path):
    """A failing migration must not advance user_version (header is transactional)."""
    import aiosqlite

    conn = await aiosqlite.connect(tmp_path / "m.db")
    bad = [(1, ["CREATE TABLE ok (id TEXT)", "THIS IS NOT VALID SQL"])]
    with pytest.raises(aiosqlite.OperationalError):
        await migrate_to_latest(conn, migrations=bad)
    version = (await (await conn.execute("PRAGMA user_version")).fetchone())[0]
    assert version == 0
    # the partial table was rolled back too
    tables = await (await conn.execute("SELECT name FROM sqlite_master WHERE name='ok'")).fetchall()
    assert tables == []
    await conn.close()


async def test_corrupt_row_raises_storage_error(adapter: SQLiteAdapter):
    # Inject a row with malformed JSON in a list column to prove errors are
    # translated to MemoryStorageError rather than leaking json/driver exceptions.
    conn = adapter._conn
    assert conn is not None
    await conn.execute(
        "INSERT INTO memory_entries "
        "(id, agent_id, type, content, scope, importance, pinned, is_consolidated, "
        " consolidated_from, confidence, confidence_source, has_conflict, tags, "
        " created_at, last_accessed_at, access_count) "
        "VALUES (?, ?, 'semantic', 'x', 'private', 0.5, 0, 0, 'NOT JSON', 1.0, "
        "'agent', 0, '[]', ?, ?, 0)",
        [str(uuid4()), str(uuid4()), datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()],
    )
    await conn.commit()
    with pytest.raises(MemoryStorageError):
        await adapter.search(MemoryQuery())
