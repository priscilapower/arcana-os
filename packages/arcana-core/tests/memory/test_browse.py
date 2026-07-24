"""Tests for the browse read path: MemoryFederation.browse + router ordering. No LLM."""

from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from arcana.memory import MemoryFederation, MemoryRouter, SQLiteAdapter
from arcana.types import MemoryQuery, MemoryScope, MemoryType, RetrievalMode
from arcana.types._utils import now_utc
from tests.support.factories import make_entry


@pytest.fixture
async def federation(tmp_path: Path):
    private = SQLiteAdapter(tmp_path / "private.db")
    await private.connect()
    fed = MemoryFederation(MemoryRouter(private=private))
    yield fed, private
    await fed.aclose()


def _browse_query(**overrides: Any) -> MemoryQuery:
    base: dict[str, Any] = {
        "scope": MemoryScope.PRIVATE,
        "retrieval_mode": RetrievalMode.keyword,
        "limit": 50,
    }
    base.update(overrides)
    return MemoryQuery(**base)


async def test_browse_orders_by_importance_without_a_query(federation):
    fed, _ = federation
    agent = uuid4()
    for imp, content in [(0.2, "low"), (0.9, "high"), (0.5, "mid")]:
        await fed.write(make_entry(agent_id=agent, type=MemoryType.SEMANTIC, content=content, importance=imp))

    results = await fed.browse(_browse_query())
    assert [e.content for e in results] == ["high", "mid", "low"]


async def test_browse_needs_no_embedder(federation):
    """browse must work offline — a text-less keyword query never resolves an embedder."""
    fed, _ = federation
    await fed.write(make_entry(agent_id=uuid4(), content="offline entry", importance=0.5))
    results = await fed.browse(_browse_query())
    assert [e.content for e in results] == ["offline entry"]


async def test_browse_keeps_aged_out_entries(federation):
    """Unlike search(), a listing surfaces entries that have decayed below their
    consolidation threshold — an audit view must show everything stored."""
    fed, _ = federation
    agent = uuid4()
    stale_created = now_utc() - timedelta(days=400)
    # A low-importance EPISODIC entry aged 400 days decays below its consolidation
    # threshold, so search() drops it — but browse() must keep it.
    stale = make_entry(
        agent_id=agent,
        type=MemoryType.EPISODIC,
        content="ancient episode",
        importance=0.15,
        created_at=stale_created,
        last_accessed_at=stale_created,
    )
    await fed.write(stale)

    assert await fed.search(MemoryQuery(agent_id=agent, scope=MemoryScope.PRIVATE, limit=50)) == []
    browsed = await fed.browse(_browse_query(agent_id=agent))
    assert [e.content for e in browsed] == ["ancient episode"]


async def test_browse_respects_type_filter_and_limit(federation):
    fed, _ = federation
    agent = uuid4()
    await fed.write(make_entry(agent_id=agent, type=MemoryType.SEMANTIC, content="s1", importance=0.8))
    await fed.write(make_entry(agent_id=agent, type=MemoryType.SEMANTIC, content="s2", importance=0.6))
    await fed.write(make_entry(agent_id=agent, type=MemoryType.EPISODIC, content="e1", importance=0.9))

    only_semantic = await fed.browse(_browse_query(type=MemoryType.SEMANTIC))
    assert {e.content for e in only_semantic} == {"s1", "s2"}

    capped = await fed.browse(_browse_query(limit=1))
    assert len(capped) == 1


async def test_browse_pinned_sorts_first(federation):
    fed, _ = federation
    agent = uuid4()
    await fed.write(make_entry(agent_id=agent, content="unpinned high", importance=0.95))
    await fed.write(make_entry(agent_id=agent, content="pinned low", importance=0.1, pinned=True))

    results = await fed.browse(_browse_query())
    assert results[0].content == "pinned low"
