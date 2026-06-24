"""Unit tests for FTS5 keyword search on SQLiteAdapter. No LLM calls."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest

from arcana.memory import SQLiteAdapter
from arcana.memory.migrations import MIGRATIONS, migrate_to_latest
from arcana.types import MemoryEntry, MemoryQuery, MemoryType
from arcana.types.memory import RetrievalMode


@pytest.fixture
async def adapter(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "memory.db")
    await a.connect()
    yield a
    await a.aclose()


def _entry(**overrides) -> MemoryEntry:
    base = dict(agent_id=uuid4(), type=MemoryType.SEMANTIC, content="placeholder", importance=0.5)
    base.update(overrides)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# BM25 ranking
# --------------------------------------------------------------------------


async def test_bm25_ranks_by_relevance_not_importance(adapter: SQLiteAdapter):
    agent = uuid4()
    # The two-term match has LOW importance; the one-term match HIGH — proving
    # lexical relevance, not importance, drives the order. Non-matching row drops out.
    await adapter.write(_entry(agent_id=agent, content="python async database tutorial guide", importance=0.1))
    await adapter.write(_entry(agent_id=agent, content="a python snippet", importance=0.9))
    await adapter.write(_entry(agent_id=agent, content="cooking pasta recipe", importance=0.9))

    got = await adapter.search(MemoryQuery(agent_id=agent, text="python database"))
    assert [e.content for e in got] == ["python async database tutorial guide", "a python snippet"]


async def test_keyword_mode_explicit(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="quarterly revenue report"))
    await adapter.write(_entry(agent_id=agent, content="lunch menu"))
    got = await adapter.search(MemoryQuery(agent_id=agent, text="revenue", retrieval_mode=RetrievalMode.keyword))
    assert len(got) == 1 and "revenue" in got[0].content


# --------------------------------------------------------------------------
# Filters compose with the text match
# --------------------------------------------------------------------------


async def test_metadata_filters_apply_on_top_of_match(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(
        _entry(agent_id=agent, content="quantum computing notes", type=MemoryType.SEMANTIC, confidence=0.3)
    )
    # Text matches, but each filter independently excludes it.
    assert await adapter.search(MemoryQuery(agent_id=agent, text="quantum", min_confidence=0.9)) == []
    assert await adapter.search(MemoryQuery(agent_id=agent, text="quantum", type=MemoryType.EPISODIC)) == []
    # Matches and passes the filter.
    ok = await adapter.search(MemoryQuery(agent_id=agent, text="quantum", min_confidence=0.1))
    assert len(ok) == 1


async def test_conflicted_excluded_by_default_in_keyword_path(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="rate limit is 1000", has_conflict=True))
    assert await adapter.search(MemoryQuery(agent_id=agent, text="rate")) == []
    incl = await adapter.search(MemoryQuery(agent_id=agent, text="rate", include_conflicted=True))
    assert len(incl) == 1


# --------------------------------------------------------------------------
# Index stays in sync with the source of truth
# --------------------------------------------------------------------------


async def test_upsert_resyncs_index(adapter: SQLiteAdapter):
    # The adapter writes via INSERT ... ON CONFLICT DO UPDATE, which fires the
    # AFTER UPDATE trigger — the old term must stop matching, the new one start.
    agent = uuid4()
    entry = _entry(agent_id=agent, content="alpha widget")
    await adapter.write(entry)
    assert len(await adapter.search(MemoryQuery(agent_id=agent, text="alpha"))) == 1

    entry.content = "beta gadget"
    await adapter.write(entry)
    assert await adapter.search(MemoryQuery(agent_id=agent, text="alpha")) == []
    assert len(await adapter.search(MemoryQuery(agent_id=agent, text="beta"))) == 1


async def test_delete_trigger_removes_from_index(adapter: SQLiteAdapter):
    # The adapter has no delete API; exercise the AFTER DELETE trigger via raw SQL
    # so index cleanup on row removal is covered.
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="deletable sprocket"))
    assert len(await adapter.search(MemoryQuery(agent_id=agent, text="sprocket"))) == 1

    conn = adapter._conn
    assert conn is not None
    await conn.execute("DELETE FROM memory_entries WHERE agent_id = ?", [str(agent)])
    await conn.commit()
    assert await adapter.search(MemoryQuery(agent_id=agent, text="sprocket")) == []


async def test_backfill_indexes_preexisting_v1_rows(tmp_path: Path):
    # Build a database at v1 only, insert a row directly, then open it with the
    # adapter so the v2 migration runs and backfills the FTS index.
    db = tmp_path / "legacy.db"
    conn = await aiosqlite.connect(db)
    await migrate_to_latest(conn, migrations=MIGRATIONS[:1])  # stop at v1
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO memory_entries (id, agent_id, type, content, created_at, last_accessed_at) "
        "VALUES (?, ?, 'semantic', 'backfilled espresso fact', ?, ?)",
        [str(uuid4()), str(uuid4()), now, now],
    )
    await conn.commit()
    await conn.close()

    a = SQLiteAdapter(db)
    await a.connect()
    got = await a.search(MemoryQuery(text="espresso"))
    assert len(got) == 1 and "espresso" in got[0].content
    await a.aclose()


# --------------------------------------------------------------------------
# Robustness — arbitrary text never raises
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["a*b", "foo:bar", '"', "   ", "(){}", "AND OR NOT", "-term", "NEAR(x y)", ""],
)
async def test_arbitrary_text_never_raises(adapter: SQLiteAdapter, text: str):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="normal content here"))
    result = await adapter.search(MemoryQuery(agent_id=agent, text=text))
    assert isinstance(result, list)  # no FTS5 syntax error, no injection


async def test_blank_text_falls_back_to_filter_order(adapter: SQLiteAdapter):
    # Punctuation-only text yields no match query → filter-and-order path, which
    # ignores text entirely and returns everything (pinned/importance ordered).
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="one"))
    await adapter.write(_entry(agent_id=agent, content="two"))
    assert len(await adapter.search(MemoryQuery(agent_id=agent, text="   :: "))) == 2


# --------------------------------------------------------------------------
# Access tracking still fires through the keyword path
# --------------------------------------------------------------------------


async def test_access_tracking_through_keyword_path(adapter: SQLiteAdapter):
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="trackable token", access_count=0))
    first = await adapter.search(MemoryQuery(agent_id=agent, text="trackable"))
    assert first[0].access_count == 1
    second = await adapter.search(MemoryQuery(agent_id=agent, text="trackable"))
    assert second[0].access_count == 2
