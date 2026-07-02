"""Integration tests for the memory federation stack.

Where the per-component suites exercise each piece in isolation, these wire the
real stack together — ``EmbeddingGateway`` → ``VectorAdapter`` (real sqlite-vec)
→ ``MemoryRouter`` → ``MemoryFederation`` — and assert end-to-end behaviour:
cross-tier promotion with vectors, the embedder health/pinning/FTS5-fallback
contract *as seen through the federation*, prune fan-out clearing vectors, and
the observability events the stack emits. No LLM calls.
"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import arcana.observability as obs
from arcana.memory import (
    EmbeddingGateway,
    MemoryFederation,
    MemoryRouter,
    SQLiteAdapter,
    VectorAdapter,
)
from arcana.models.adapters.embedding import AdapterHealth, EmbeddingAdapter
from arcana.types import (
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    PruneMode,
    PrunePolicy,
    RetrievalMode,
)

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

_AXES = ("alpha", "beta", "gamma", "delta")


class KeywordEmbedder(EmbeddingAdapter):
    """Deterministic embedder: vector[i] = count of ``_AXES[i]`` in the text.

    Same shape as the vector-adapter suite's stand-in, so semantic ranking is
    exact. ``healthy``/``family``/``name`` drive the gateway-through-federation
    health and pinning paths.
    """

    def __init__(self, *, name: str = "kw-embed", family: str | None = None, healthy: bool = True) -> None:
        self._name = name
        self._family = family or name
        self._healthy = healthy
        self.embed_calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return len(_AXES)

    @property
    def model_family(self) -> str:
        return self._family

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        vec = [float(text.lower().count(ax)) for ax in _AXES]
        if not any(vec):
            vec[0] = 1.0  # cosine distance is undefined for a zero vector
        return vec

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id=self._name, healthy=self._healthy)


async def _vector_tier(db_path: Path, *embedders: EmbeddingAdapter) -> VectorAdapter:
    adapter = VectorAdapter(SQLiteAdapter(db_path), EmbeddingGateway(list(embedders)))
    await adapter.connect()
    return adapter


def _entry(**overrides: Any) -> MemoryEntry:
    base: dict[str, Any] = dict(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="alpha",
        importance=0.5,
        scope=MemoryScope.PRIVATE,
    )
    base.update(overrides)
    return MemoryEntry(**base)


async def _vector_count(tier: VectorAdapter) -> int:
    conn = tier._sqlite.connection
    rows = await (await conn.execute("SELECT COUNT(*) FROM memory_vectors")).fetchall()
    return int(rows[0][0])


@pytest.fixture
def audit_log(tmp_path: Path):
    """Configure the global audit log to a temp dir, restoring it afterwards."""
    previous = obs.get_audit_log()
    obs.configure_observability(tmp_path / "obs")
    log = obs.get_audit_log()
    assert log is not None
    yield log
    obs._audit_log = previous


# --------------------------------------------------------------------------
# Federation over real vector-backed tiers
# --------------------------------------------------------------------------


async def test_promotion_carries_the_vector_into_global(tmp_path: Path):
    private = await _vector_tier(tmp_path / "private.db", KeywordEmbedder())
    global_ = await _vector_tier(tmp_path / "global.db", KeywordEmbedder())
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    agent = uuid4()
    entry = _entry(agent_id=agent, content="alpha alpha", importance=0.95, scope=MemoryScope.PRIVATE)
    await fed.write(entry)

    # Federated read sees it once despite living in both tiers.
    merged = await fed.search(MemoryQuery(agent_id=agent, retrieval_mode=RetrievalMode.semantic, text="alpha"))
    assert [e.id for e in merged] == [entry.id]

    # The global tier holds a real, vector-indexed copy — a direct semantic
    # search against it (not just a row read) surfaces the promoted entry.
    in_global = await global_.search(
        MemoryQuery(scope=MemoryScope.GLOBAL, text="alpha", retrieval_mode=RetrievalMode.semantic)
    )
    assert [e.id for e in in_global] == [entry.id]
    assert await _vector_count(global_) == 1

    await private.aclose()
    await global_.aclose()


async def test_federated_search_returns_matches_ranked_by_importance(tmp_path: Path):
    # Federation re-ranks merged candidates by importance (per-tier semantic
    # order is internal); both matching entries come back, importance-ordered.
    private = await _vector_tier(tmp_path / "private.db", KeywordEmbedder())
    fed = MemoryFederation(MemoryRouter(private=private))

    agent = uuid4()
    strong = _entry(agent_id=agent, content="alpha beta", importance=0.8, scope=MemoryScope.PRIVATE)
    weak = _entry(agent_id=agent, content="alpha gamma", importance=0.2, scope=MemoryScope.PRIVATE)
    await fed.write(weak)
    await fed.write(strong)

    got = await fed.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))
    assert [e.id for e in got] == [strong.id, weak.id]

    await private.aclose()


# --------------------------------------------------------------------------
# Embedder health / pinning / FTS5 fallback — through the federation
# --------------------------------------------------------------------------


async def test_federation_falls_back_to_keyword_when_pinned_model_unhealthy(tmp_path: Path):
    db = tmp_path / "private.db"
    agent = uuid4()

    # First run pins the database to a healthy embedder and indexes a vector.
    healthy = await _vector_tier(db, KeywordEmbedder(name="kw", healthy=True))
    await MemoryFederation(MemoryRouter(private=healthy)).write(
        _entry(agent_id=agent, content="alpha", scope=MemoryScope.PRIVATE)
    )
    await healthy.aclose()

    # Reopen with the pinned model now unhealthy: no compatible embedder, so the
    # federation must still answer via the FTS5 keyword leg rather than nothing.
    down = await _vector_tier(db, KeywordEmbedder(name="kw", healthy=False))
    fed = MemoryFederation(MemoryRouter(private=down))
    results = await fed.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.keyword))
    assert len(results) == 1
    await down.aclose()


async def test_federation_keeps_semantic_via_family_fallback(tmp_path: Path):
    db = tmp_path / "private.db"
    agent = uuid4()

    tier1 = await _vector_tier(db, KeywordEmbedder(name="nomic-embed-text", family="nomic"))
    await MemoryFederation(MemoryRouter(private=tier1)).write(
        _entry(agent_id=agent, content="alpha alpha", scope=MemoryScope.PRIVATE)
    )
    await tier1.aclose()

    # Exact pinned model is down, but a same-family sibling is healthy: the
    # federation stays on the semantic path instead of degrading to FTS5.
    down = KeywordEmbedder(name="nomic-embed-text", family="nomic", healthy=False)
    sibling = KeywordEmbedder(name="nomic-embed-text-v1.5", family="nomic", healthy=True)
    reopened = await _vector_tier(db, down, sibling)
    fed = MemoryFederation(MemoryRouter(private=reopened))

    results = await fed.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))
    assert sibling.embed_calls == 1  # the sibling actually produced the query vector
    assert [e.content for e in results] == ["alpha alpha"]
    await reopened.aclose()


# --------------------------------------------------------------------------
# Prune fan-out clears vectors across tiers
# --------------------------------------------------------------------------


async def test_federation_purge_clears_vectors_across_tiers(tmp_path: Path):
    private = await _vector_tier(tmp_path / "private.db", KeywordEmbedder())
    global_ = await _vector_tier(tmp_path / "global.db", KeywordEmbedder())
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    agent = uuid4()
    await fed.write(_entry(agent_id=agent, content="alpha", importance=0.05, scope=MemoryScope.PRIVATE))
    await fed.write(_entry(agent_id=agent, content="beta", importance=0.05, scope=MemoryScope.GLOBAL))
    assert await _vector_count(private) == 1
    assert await _vector_count(global_) == 1

    report = await fed.prune(PrunePolicy(min_importance=0.1, mode=PruneMode.PURGE))
    assert report.purged == 2
    assert report.tiers == 2

    # Rows and their vectors are gone from both tiers.
    assert await _vector_count(private) == 0
    assert await _vector_count(global_) == 0
    assert await fed.search(MemoryQuery(agent_id=agent, include_archived=True)) == []

    await private.aclose()
    await global_.aclose()


# --------------------------------------------------------------------------
# Observability — the events the stack emits
# --------------------------------------------------------------------------


async def test_write_emits_audit_event(audit_log, tmp_path: Path):
    adapter = SQLiteAdapter(tmp_path / "m.db")
    await adapter.connect()
    await adapter.write(_entry(importance=0.7))
    await adapter.aclose()

    events = audit_log.tail(event_type="memory_write")
    assert len(events) == 1
    assert events[0]["importance"] == 0.7


async def test_search_emits_read_event(audit_log, tmp_path: Path):
    adapter = SQLiteAdapter(tmp_path / "m.db")
    await adapter.connect()
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="alpha"))
    await adapter.search(MemoryQuery(agent_id=agent, text="alpha"))
    await adapter.aclose()

    events = audit_log.tail(event_type="memory_read")
    assert events and events[-1]["query_text"] == "alpha"


async def test_prune_emits_prune_event(audit_log, tmp_path: Path):
    adapter = SQLiteAdapter(tmp_path / "m.db")
    await adapter.connect()
    await adapter.write(_entry(importance=0.05))
    await adapter.prune(PrunePolicy(min_importance=0.1))
    await adapter.aclose()

    events = audit_log.tail(event_type="memory_prune")
    assert len(events) == 1
    assert events[0]["archived"] == 1
    assert events[0]["scanned"] == 1


async def test_full_pipeline_emits_all_event_types(audit_log, tmp_path: Path):
    # One federated write → search → prune produces all three memory events.
    private = SQLiteAdapter(tmp_path / "private.db")
    await private.connect()
    fed = MemoryFederation(MemoryRouter(private=private))

    agent = uuid4()
    await fed.write(_entry(agent_id=agent, content="alpha", importance=0.05))
    await fed.search(MemoryQuery(agent_id=agent, text="alpha"))
    await fed.prune(PrunePolicy(min_importance=0.1))
    await private.aclose()

    kinds = {e["type"] for e in audit_log.tail()}
    assert {"memory_write", "memory_read", "memory_prune"} <= kinds
