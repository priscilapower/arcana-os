"""Unit tests for EdgeStore (the memory_edges layer). No LLM calls."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from arcana.memory import EdgeStore, SQLiteAdapter
from arcana.memory.migrations import latest_version
from arcana.types import MemoryEdge


@pytest.fixture
async def store(tmp_path: Path):
    s = EdgeStore(SQLiteAdapter(tmp_path / "memory.db"))
    await s.connect()
    yield s
    await s.aclose()


def _edge(src: UUID, dst: UUID, *, relation: str = "references", source: str = "wikilink") -> MemoryEdge:
    return MemoryEdge(src_id=src, dst_id=dst, relation=relation, source=source)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


async def test_memory_edges_migration_present():
    # The edge table ships as the latest migration.
    assert latest_version() == 4


async def test_count_starts_zero(store: EdgeStore):
    assert await store.count() == 0
    assert await store.all() == []


# --------------------------------------------------------------------------
# Upsert & idempotency
# --------------------------------------------------------------------------


async def test_upsert_and_read_back(store: EdgeStore):
    a, b = uuid4(), uuid4()
    written = await store.upsert([_edge(a, b)])
    assert written == 1
    [edge] = await store.all()
    assert edge.src_id == a and edge.dst_id == b
    assert edge.relation == "references" and edge.source == "wikilink"
    assert edge.confidence == 1.0


async def test_upsert_is_idempotent_on_key(store: EdgeStore):
    a, b = uuid4(), uuid4()
    await store.upsert([_edge(a, b)])
    await store.upsert([_edge(a, b)])  # same (src, dst, relation) → replace, not duplicate
    assert await store.count() == 1


async def test_same_endpoints_different_relation_coexist(store: EdgeStore):
    a, b = uuid4(), uuid4()
    await store.upsert([_edge(a, b, relation="references"), _edge(a, b, relation="mentions")])
    assert await store.count() == 2


async def test_upsert_empty_is_noop(store: EdgeStore):
    assert await store.upsert([]) == 0
    assert await store.count() == 0


# --------------------------------------------------------------------------
# replace_source
# --------------------------------------------------------------------------


async def test_replace_source_swaps_only_its_own_edges(store: EdgeStore):
    a, b, c = uuid4(), uuid4(), uuid4()
    await store.upsert([_edge(a, b, source="manual")])
    # Replace the wikilink set; the manual edge is untouched.
    await store.replace_source("wikilink", [_edge(a, c, source="wikilink")])
    pairs = {(e.src_id, e.dst_id, e.source) for e in await store.all()}
    assert (a, b, "manual") in pairs
    assert (a, c, "wikilink") in pairs
    assert len(pairs) == 2


async def test_replace_source_drops_removed_edges(store: EdgeStore):
    a, b, c = uuid4(), uuid4(), uuid4()
    await store.replace_source("wikilink", [_edge(a, b), _edge(a, c)])
    assert await store.count() == 2
    # Re-index with only one edge → the other is gone.
    await store.replace_source("wikilink", [_edge(a, b)])
    remaining = await store.all()
    assert len(remaining) == 1 and remaining[0].dst_id == b


async def test_replace_source_empty_clears(store: EdgeStore):
    a, b = uuid4(), uuid4()
    await store.replace_source("wikilink", [_edge(a, b)])
    assert await store.replace_source("wikilink", []) == 0
    assert await store.count() == 0


# --------------------------------------------------------------------------
# Traversal
# --------------------------------------------------------------------------


async def test_outgoing_and_incoming(store: EdgeStore):
    a, b, c = uuid4(), uuid4(), uuid4()
    await store.upsert([_edge(a, b), _edge(a, c), _edge(b, a)])
    out = {e.dst_id for e in await store.outgoing(a)}
    assert out == {b, c}
    incoming = {e.src_id for e in await store.incoming(a)}
    assert incoming == {b}


async def test_neighbors_dedups_both_directions(store: EdgeStore):
    a, b, c = uuid4(), uuid4(), uuid4()
    # a→b, a→c, b→a : neighbors of a are b and c (b appears both ways → once).
    await store.upsert([_edge(a, b), _edge(a, c), _edge(b, a)])
    neighbors = await store.neighbors(a)
    assert set(neighbors) == {b, c}
    assert len(neighbors) == 2  # deduped


async def test_neighbors_empty_for_isolated_node(store: EdgeStore):
    assert await store.neighbors(uuid4()) == []
