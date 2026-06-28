"""VectorAdapter — semantic ``MemoryAdapter`` over sqlite-vec.

Layers a ``vec0`` vector index onto a :class:`SQLiteAdapter`'s database: same
``memory.db``, same connection, so row + vector writes stay consistent (the
pattern the FTS5 sync triggers already follow). The embedder is resolved through
an :class:`EmbeddingGateway`, honouring the database's model pin. When no
compatible embedder is healthy — or sqlite-vec is unavailable — search falls back
to FTS5 keyword retrieval rather than returning nothing.

The vec0 table's dimension is fixed at CREATE time and determined at runtime by
the resolved embedder, so it cannot be a static ``PRAGMA user_version`` migration.
It is created lazily on the first embedded write, together with the
``embedding_meta`` pin row — that paired act is what locks the database to a model.
Vectors are L2-normalised on write and query; combined with the index's cosine
distance metric, ranking is by cosine similarity.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable

import aiosqlite

from arcana.memory.adapters import _sql
from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.memory.errors import MemoryStorageError
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.types import AdapterCapabilities, EmbeddingMeta, MemoryEntry, MemoryQuery, RetrievalMode

logger = logging.getLogger("arcana.memory.vector")

#: Floor on KNN candidates fetched before metadata filtering, so a small
#: ``query.limit`` still has a pool to survive scope/importance/time filters.
_MIN_CANDIDATES = 50


def _l2_normalize(vec: list[float]) -> list[float]:
    """Scale ``vec`` to unit length; a zero vector is returned unchanged."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _minmax(scores: dict[str, float]) -> dict[str, float]:
    """Scale scores to ``[0, 1]`` for hybrid fusion.

    A single candidate — or a leg where every score ties — maps to ``1.0``
    (uniformly relevant) rather than dividing by a zero span. Inputs are
    higher-is-better relevances, so the best score lands at 1.0, the worst at 0.0.
    """
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    if hi == lo:
        return {key: 1.0 for key in scores}
    span = hi - lo
    return {key: (value - lo) / span for key, value in scores.items()}


class VectorAdapter:
    """Semantic memory backend: sqlite-vec KNN over a ``SQLiteAdapter``'s database.

    Implements the ``MemoryAdapter`` protocol (``search``/``write``). Composes —
    does not subclass — a ``SQLiteAdapter``, reusing its row storage, FTS5 index,
    importance-based promotion, and access tracking unchanged, and adds the vector
    layer on top.
    """

    def __init__(
        self,
        sqlite: SQLiteAdapter,
        gateway: EmbeddingGateway,
        *,
        candidate_multiplier: int = 4,
        vector_weight: float = 0.7,
        bm25_weight: float = 0.3,
    ) -> None:
        self._sqlite = sqlite
        self._gateway = gateway
        #: KNN oversampling factor: fetch ``limit * multiplier`` neighbours so
        #: metadata filtering still leaves enough to fill ``limit``.
        self._candidate_multiplier = candidate_multiplier
        #: Hybrid fusion weights, normalised to sum 1 so only their ratio matters.
        total = vector_weight + bm25_weight
        if total <= 0:
            raise ValueError("vector_weight + bm25_weight must be positive")
        self._vector_weight = vector_weight / total
        self._bm25_weight = bm25_weight / total
        self._vec_ok = False
        self._vec_attempted = False
        self._serialize: Callable[[list[float]], bytes] | None = None
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect the underlying store and load the sqlite-vec extension once.

        Idempotent. If sqlite-vec is missing or the SQLite build cannot load
        extensions, the adapter degrades to keyword-only (one visible warning)
        rather than failing — arcana stays usable without the ``[vector]`` extra.
        """
        await self._sqlite.connect()
        if self._vec_attempted:
            return
        self._vec_attempted = True
        try:
            import sqlite_vec  # lazy: module stays import-safe without the extra

            self._serialize = sqlite_vec.serialize_float32
            conn = self._sqlite.connection
            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)
            self._vec_ok = True
        except Exception as exc:  # ImportError, sqlite3 build without load_extension, …
            self._vec_ok = False
            self._warn_once(
                "no-extension",
                f"sqlite-vec unavailable ({exc}); memory search is keyword-only. "
                "Install the `arcana-os[vector]` extra to enable semantic search.",
            )

    async def aclose(self) -> None:
        """Close the underlying store. Safe to call more than once."""
        await self._sqlite.aclose()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(supports_vector=self._vec_ok, supports_full_text=True)

    # ------------------------------------------------------------------
    # MemoryAdapter protocol
    # ------------------------------------------------------------------

    async def write(self, entry: MemoryEntry) -> None:
        """Store ``entry`` (row + FTS5 + promotion) and index its vector.

        Resolves the embedder via the gateway. With no healthy embedder the entry
        is still stored and keyword-searchable, just without a vector. The first
        embedded write pins the database to the model (``embedding_meta``) and
        creates the dimension-sized vec0 index.
        """
        await self.connect()
        conn = self._sqlite.connection

        embedder: EmbeddingAdapter | None = None
        meta: EmbeddingMeta | None = None
        if self._vec_ok:
            meta = await self._read_meta(conn)
            embedder = await self._gateway.resolve(meta)

        if embedder is None:
            if self._vec_ok:
                self._warn_once(
                    "write-fallback",
                    "No healthy embedding adapter; storing memory without a vector (keyword-only).",
                )
            await self._sqlite.write(entry)
            return

        vec = _l2_normalize(entry.embedding if entry.embedding is not None else await embedder.embed(entry.content))

        # Dimension safety: never write a vector that disagrees with the embedder
        # or the database pin — a wrong-width vector would corrupt the index.
        if len(vec) != embedder.dimensions:
            raise MemoryStorageError(
                f"embedder {embedder.model_name} produced a {len(vec)}-d vector, expected {embedder.dimensions}"
            )
        if meta is not None and len(vec) != meta.dimensions:
            raise MemoryStorageError(
                f"vector dimension {len(vec)} does not match database pin {meta.dimensions} "
                f"(model {meta.model_name}); refusing to corrupt the index"
            )

        entry.embedding = vec

        # First embedding: create the dimension-sized index and pin the model.
        if meta is None:
            await conn.execute(_sql.vec_table_ddl(embedder.dimensions))
            await self._pin_meta(conn, embedder)

        # Row first (memory_entries + FTS5 + promotion), then the vector.
        await self._sqlite.write(entry)
        serialize = self._serialize
        assert serialize is not None  # noqa: S101 — set whenever _vec_ok (embedder resolved)
        try:
            await conn.execute(_sql.VEC_DELETE, (str(entry.id),))
            await conn.execute(_sql.VEC_INSERT, (str(entry.id), serialize(vec)))
            await conn.execute(_sql.BUMP_ENTRY_COUNT)
            await conn.commit()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"vector write failed for entry {entry.id}: {exc}") from exc

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Return entries matching ``query``, semantically when possible.

        Semantic KNN runs for ``semantic``/``hybrid`` queries that carry usable
        text against a pinned database with a healthy compatible embedder.
        Otherwise — ``keyword`` mode, no text, an unpinned database, or no healthy
        embedder — results come from the keyword/filter path on the underlying
        store.
        """
        await self.connect()
        conn = self._sqlite.connection

        match_text = _sql.to_match_query(query.text) if query.text else None
        use_vector = self._vec_ok and query.retrieval_mode != RetrievalMode.keyword and match_text is not None

        if use_vector:
            meta = await self._read_meta(conn)
            # An unpinned database has no vectors to search; keyword path serves it
            # without a warning. A pin with no healthy embedder is the real fallback.
            if meta is not None:
                embedder = await self._gateway.resolve(meta)
                if embedder is not None:
                    if query.retrieval_mode == RetrievalMode.hybrid:
                        return await self._hybrid_search(conn, query, embedder, meta)
                    return await self._semantic_search(conn, query, embedder, meta)
                self._warn_once(
                    "search-fallback",
                    "Pinned embedding model is unavailable; falling back to FTS5 keyword search.",
                )

        return await self._sqlite.search(query)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _semantic_search(
        self,
        conn: aiosqlite.Connection,
        query: MemoryQuery,
        embedder: EmbeddingAdapter,
        meta: EmbeddingMeta,
    ) -> list[MemoryEntry]:
        started = time.perf_counter()
        qvec = _l2_normalize(await embedder.embed(query.text or ""))
        if len(qvec) != meta.dimensions:
            # The gateway guards family/dimension compatibility, so this is a
            # defensive belt-and-braces check rather than an expected path.
            self._warn_once(
                "search-fallback",
                "Query embedding dimension does not match the database pin; falling back to FTS5.",
            )
            return await self._sqlite.search(query)

        serialize = self._serialize
        assert serialize is not None  # noqa: S101 — set whenever _vec_ok (we are past the guard)
        k = max(query.limit * self._candidate_multiplier, _MIN_CANDIDATES)
        try:
            cursor = await conn.execute(_sql.VEC_KNN, (serialize(qvec), k))
            knn = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"vector search failed: {exc}") from exc
        if not knn:
            return []

        # entry_id -> KNN rank (ascending distance); used to re-order after the
        # metadata-filtered rehydrate, which SQL returns in arbitrary order.
        rank = {row[0]: i for i, row in enumerate(knn)}
        sql, params = _sql.fetch_by_ids(query, list(rank))
        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"vector row fetch failed: {exc}") from exc

        try:
            entries = [_sql.row_to_entry(row) for row in rows]
        except Exception as exc:
            raise MemoryStorageError(f"failed to decode stored memory row: {exc}") from exc

        entries.sort(key=lambda e: rank[str(e.id)])
        entries = entries[: query.limit]

        await self._sqlite.record_read(query, entries, started)
        return entries

    async def _hybrid_search(
        self,
        conn: aiosqlite.Connection,
        query: MemoryQuery,
        embedder: EmbeddingAdapter,
        meta: EmbeddingMeta,
    ) -> list[MemoryEntry]:
        """Fuse the vector (cosine) and keyword (BM25) legs into one ranking.

        Each leg is converted to a higher-is-better relevance and min-max
        normalised over the surviving candidates, then combined as
        ``vector_weight * vNorm + bm25_weight * bm25Norm``. Per-query
        normalisation keeps the legs' incompatible scales from letting one
        dominate; an id found by only one leg contributes 0 for the other.
        """
        started = time.perf_counter()
        qvec = _l2_normalize(await embedder.embed(query.text or ""))
        if len(qvec) != meta.dimensions:
            self._warn_once(
                "search-fallback",
                "Query embedding dimension does not match the database pin; falling back to FTS5.",
            )
            return await self._sqlite.search(query)

        serialize = self._serialize
        assert serialize is not None  # noqa: S101 — set whenever _vec_ok (we are past the guard)
        k = max(query.limit * self._candidate_multiplier, _MIN_CANDIDATES)

        # Vector leg: id -> cosine distance (smaller = better).
        try:
            cursor = await conn.execute(_sql.VEC_KNN, (serialize(qvec), k))
            vec_rows = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"vector search failed: {exc}") from exc
        distances = {row[0]: row[1] for row in vec_rows}

        # BM25 leg: id -> bm25 score (smaller = better). Reuses the keyword
        # candidate pool; ``match`` is non-None here (search() gated on it).
        bm25: dict[str, float] = {}
        match = _sql.to_match_query(query.text or "")
        if match is not None:
            sql, params = _sql.bm25_candidates(query, match, k)
            try:
                cursor = await conn.execute(sql, params)
                bm_rows = await cursor.fetchall()
            except aiosqlite.Error as exc:
                raise MemoryStorageError(f"keyword search failed: {exc}") from exc
            bm25 = {row[0]: row[1] for row in bm_rows}

        union = list({*distances, *bm25})
        if not union:
            return []

        # One filtered fetch over the union applies the metadata filters to both
        # legs' candidates at once.
        sql, params = _sql.fetch_by_ids(query, union)
        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"vector row fetch failed: {exc}") from exc
        try:
            entries = [_sql.row_to_entry(row) for row in rows]
        except Exception as exc:
            raise MemoryStorageError(f"failed to decode stored memory row: {exc}") from exc

        # Normalise each leg over the surviving ids (negate so higher = better).
        survivors = [str(e.id) for e in entries]
        vnorm = _minmax({eid: -distances[eid] for eid in survivors if eid in distances})
        bnorm = _minmax({eid: -bm25[eid] for eid in survivors if eid in bm25})

        def fused(entry: MemoryEntry) -> float:
            eid = str(entry.id)
            return self._vector_weight * vnorm.get(eid, 0.0) + self._bm25_weight * bnorm.get(eid, 0.0)

        entries.sort(key=fused, reverse=True)
        entries = entries[: query.limit]

        await self._sqlite.record_read(query, entries, started)
        return entries

    async def _read_meta(self, conn: aiosqlite.Connection) -> EmbeddingMeta | None:
        cursor = await conn.execute(_sql.READ_EMBEDDING_META)
        row = await cursor.fetchone()
        return _sql.meta_row_to_model(row) if row is not None else None

    async def _pin_meta(self, conn: aiosqlite.Connection, embedder: EmbeddingAdapter) -> None:
        meta = EmbeddingMeta(model_name=embedder.model_name, dimensions=embedder.dimensions)
        await conn.execute(
            _sql.INSERT_EMBEDDING_META,
            (meta.model_name, meta.dimensions, meta.first_used_at.isoformat(), meta.entry_count),
        )

    def _warn_once(self, key: str, message: str) -> None:
        """Emit a warning at most once per category for this adapter instance."""
        if key not in self._warned:
            logger.warning(message)
            self._warned.add(key)
