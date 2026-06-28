"""Unit tests for VectorAdapter (sqlite-vec). No LLM calls.

A deterministic ``KeywordEmbedder`` stands in for a real embedding provider so
semantic ranking is exact and reproducible: each axis of the vector counts a
keyword's occurrences, so "alpha" embeds nearest to content containing "alpha".
sqlite-vec itself is exercised for real (installed via ``arcana-os[vector]``).
"""

import logging
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import EmbeddingGateway, MemoryStorageError, SQLiteAdapter, VectorAdapter
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.types import AdapterHealth, MemoryEntry, MemoryQuery, MemoryScope, MemoryType, RetrievalMode

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

_AXES = ("alpha", "beta", "gamma", "delta")


class KeywordEmbedder(EmbeddingAdapter):
    """Deterministic embedder: vector[i] = count of ``_AXES[i]`` in the text.

    Configurable model_name/family/health/dims so the same class drives the
    pinning, family-fallback, dimension-guard, and health-fallback tests.
    """

    def __init__(
        self,
        *,
        name: str = "kw-embed",
        family: str | None = None,
        healthy: bool = True,
        dims: int = 4,
    ) -> None:
        self._name = name
        self._family = family or name
        self._healthy = healthy
        self._dims = dims
        self.embed_calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return self._dims

    @property
    def model_family(self) -> str:
        return self._family

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        low = text.lower()
        vec = [float(low.count(ax)) for ax in _AXES]
        # Pad/truncate to the declared dimension (drives the dim-mismatch test).
        vec = (vec + [0.0] * self._dims)[: self._dims]
        if not any(vec):
            vec[0] = 1.0  # avoid a zero vector — cosine distance is undefined for it
        return vec

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id=self._name, healthy=self._healthy)


def _entry(**overrides: object) -> MemoryEntry:
    base: dict[str, object] = dict(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="alpha",
        importance=0.5,
    )
    base.update(overrides)
    return MemoryEntry(**base)  # type: ignore[arg-type]


async def _build(db_path: Path, *embedders: EmbeddingAdapter) -> VectorAdapter:
    """A connected VectorAdapter over ``db_path`` with the given embedder priority."""
    adapter = VectorAdapter(SQLiteAdapter(db_path), EmbeddingGateway(list(embedders)))
    await adapter.connect()
    return adapter


@pytest.fixture
async def vec(tmp_path: Path):
    adapter = await _build(tmp_path / "memory.db", KeywordEmbedder())
    yield adapter
    await adapter.aclose()


# --------------------------------------------------------------------------
# Semantic search & pinning
# --------------------------------------------------------------------------


async def test_semantic_search_orders_by_similarity(vec: VectorAdapter):
    agent = uuid4()
    near = _entry(agent_id=agent, content="alpha alpha")  # closest to "alpha"
    mid = _entry(agent_id=agent, content="alpha beta")
    far = _entry(agent_id=agent, content="beta gamma")
    for e in (far, mid, near):
        await vec.write(e)

    results = await vec.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))

    assert [e.id for e in results] == [near.id, mid.id, far.id]


async def test_first_write_pins_and_creates_index(vec: VectorAdapter):
    await vec.write(_entry(content="alpha"))
    await vec.write(_entry(content="beta"))

    conn = vec._sqlite.connection
    table = await (
        await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_vectors'")
    ).fetchone()
    assert table is not None

    meta = [
        tuple(r)
        for r in await (
            await conn.execute("SELECT model_name, dimensions, entry_count FROM embedding_meta")
        ).fetchall()
    ]
    assert meta == [("kw-embed", 4, 2)]  # pinned to the embedder; count tracks writes


async def test_capabilities_reports_vector(vec: VectorAdapter):
    caps = vec.capabilities()
    assert caps.supports_vector is True
    assert caps.supports_full_text is True


# --------------------------------------------------------------------------
# Dimension & family safety
# --------------------------------------------------------------------------


async def test_dimension_mismatch_with_pin_raises(tmp_path: Path):
    db = tmp_path / "memory.db"
    a = await _build(db, KeywordEmbedder(name="m", dims=4))
    await a.write(_entry(content="alpha"))  # pins to (m, 4)
    await a.aclose()

    # Same model name reappears at a different width — exactly the corruption the
    # pin exists to prevent. The write must be refused, not silently indexed.
    b = await _build(db, KeywordEmbedder(name="m", dims=8))
    with pytest.raises(MemoryStorageError, match="does not match database pin"):
        await b.write(_entry(content="alpha"))
    await b.aclose()


async def test_family_fallback_keeps_semantic(tmp_path: Path):
    db = tmp_path / "memory.db"
    tier1 = KeywordEmbedder(name="nomic-embed-text", family="nomic", healthy=True)
    a = await _build(db, tier1)
    agent = uuid4()
    await a.write(_entry(agent_id=agent, content="alpha alpha"))
    await a.write(_entry(agent_id=agent, content="beta"))
    await a.aclose()

    # Exact model down; a same-family sibling is healthy → still semantic, not FTS5.
    down = KeywordEmbedder(name="nomic-embed-text", family="nomic", healthy=False)
    sibling = KeywordEmbedder(name="nomic-embed-text-v1.5", family="nomic", healthy=True)
    b = await _build(db, down, sibling)
    results = await b.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))

    assert sibling.embed_calls == 1  # the sibling actually served the query vector
    assert results and "alpha" in results[0].content
    await b.aclose()


# --------------------------------------------------------------------------
# Fallback to keyword (FTS5)
# --------------------------------------------------------------------------


async def test_write_without_embedder_stores_keyword_searchable(tmp_path: Path, caplog):
    caplog.set_level(logging.WARNING, logger="arcana.memory.vector")
    a = await _build(tmp_path / "memory.db", KeywordEmbedder(healthy=False))
    agent = uuid4()
    await a.write(_entry(agent_id=agent, content="alpha"))

    # No vector written, but the row is still there and keyword-searchable.
    results = await a.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.keyword))
    assert len(results) == 1
    assert any("without a vector" in r.message for r in caplog.records)
    await a.aclose()


async def test_search_falls_back_when_pinned_model_unhealthy(tmp_path: Path, caplog):
    db = tmp_path / "memory.db"
    healthy = KeywordEmbedder(name="m", family="m", healthy=True)
    a = await _build(db, healthy)
    agent = uuid4()
    await a.write(_entry(agent_id=agent, content="alpha"))  # pins + indexes
    await a.aclose()

    caplog.set_level(logging.WARNING, logger="arcana.memory.vector")
    down = KeywordEmbedder(name="m", family="m", healthy=False)
    b = await _build(db, down)
    results = await b.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))

    assert len(results) == 1  # served by FTS5
    assert down.embed_calls == 0  # keyword path never embeds
    assert any("falling back to FTS5" in r.message for r in caplog.records)
    await b.aclose()


async def test_degrades_to_keyword_without_extension(tmp_path: Path, monkeypatch, caplog):
    # Hiding the module makes ``import sqlite_vec`` raise — the no-extra install.
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    caplog.set_level(logging.WARNING, logger="arcana.memory.vector")

    adapter = VectorAdapter(SQLiteAdapter(tmp_path / "memory.db"), EmbeddingGateway([KeywordEmbedder()]))
    await adapter.connect()

    assert adapter.capabilities().supports_vector is False
    agent = uuid4()
    await adapter.write(_entry(agent_id=agent, content="alpha"))
    results = await adapter.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))
    assert len(results) == 1  # keyword-only, no crash
    assert any("keyword-only" in r.message for r in caplog.records)
    await adapter.aclose()


# --------------------------------------------------------------------------
# Mode routing & metadata filtering
# --------------------------------------------------------------------------


async def test_keyword_mode_skips_embedding(vec: VectorAdapter):
    embedder = vec._gateway._adapters[0]
    assert isinstance(embedder, KeywordEmbedder)
    agent = uuid4()
    await embedder.embed("warm up")  # writes embed once on write below; reset after
    embedder.embed_calls = 0

    await vec.write(_entry(agent_id=agent, content="alpha"))
    calls_after_write = embedder.embed_calls
    await vec.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.keyword))

    assert embedder.embed_calls == calls_after_write  # search did not embed


async def test_no_text_uses_filter_path(vec: VectorAdapter):
    embedder = vec._gateway._adapters[0]
    assert isinstance(embedder, KeywordEmbedder)
    agent = uuid4()
    await vec.write(_entry(agent_id=agent, content="alpha", importance=0.2))
    await vec.write(_entry(agent_id=agent, content="alpha", importance=0.9))
    embedder.embed_calls = 0

    results = await vec.search(MemoryQuery(agent_id=agent, retrieval_mode=RetrievalMode.semantic))

    assert embedder.embed_calls == 0  # no query text → no vector search
    assert [e.importance for e in results] == [0.9, 0.2]  # filter-and-order by importance


async def test_metadata_filters_apply_to_semantic(vec: VectorAdapter):
    agent = uuid4()
    keep = _entry(agent_id=agent, content="alpha alpha", scope=MemoryScope.PRIVATE, importance=0.8)
    drop_scope = _entry(agent_id=agent, content="alpha", scope=MemoryScope.SHARED, pool_name="p", importance=0.8)
    drop_imp = _entry(agent_id=agent, content="alpha", scope=MemoryScope.PRIVATE, importance=0.1)
    for e in (keep, drop_scope, drop_imp):
        await vec.write(e)

    results = await vec.search(
        MemoryQuery(
            agent_id=agent,
            text="alpha",
            retrieval_mode=RetrievalMode.semantic,
            scope=MemoryScope.PRIVATE,
            min_importance=0.5,
        )
    )

    assert [e.id for e in results] == [keep.id]


async def test_upsert_reembeds_on_changed_content(vec: VectorAdapter):
    agent = uuid4()
    entry = _entry(agent_id=agent, content="beta")
    await vec.write(entry)

    # Rewrite same id with new content and no precomputed embedding → re-embedded.
    entry.content = "alpha alpha"
    entry.embedding = None
    await vec.write(entry)

    results = await vec.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))
    assert len(results) == 1
    assert results[0].content == "alpha alpha"

    conn = vec._sqlite.connection
    count = await (await conn.execute("SELECT count(*) FROM memory_vectors")).fetchone()
    assert count is not None and count[0] == 1  # replaced in place, not duplicated
