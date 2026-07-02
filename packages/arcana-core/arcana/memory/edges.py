"""EdgeStore — the ``memory_edges`` read/write layer of the memory graph.

A thin async store over the ``memory_edges`` table (schema v4), composing a
:class:`SQLiteAdapter` so edges live in the same database as the rows they relate —
the property-graph-in-SQLite design, no separate engine. Endpoints are stable node
ids, so an edge may relate nodes that live in any tier (a folder connector's
``uuid5`` note id is a valid endpoint even though it is not a ``memory_entries`` row).

The ``(src_id, dst_id, relation)`` primary key makes writes idempotent. A producer
tags its edges with a ``source`` and replaces exactly that set on re-index via
:meth:`replace_source`, so re-running an extractor stays convergent as links are
added, removed, or re-pointed.
"""

from collections.abc import Iterable
from uuid import UUID

import aiosqlite

from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.errors import MemoryStorageError
from arcana.types import MemoryEdge

_INSERT = (
    "INSERT OR REPLACE INTO memory_edges (src_id, dst_id, relation, confidence, source, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_DELETE_BY_SOURCE = "DELETE FROM memory_edges WHERE source = ?"
_SELECT_ALL = "SELECT src_id, dst_id, relation, confidence, source, created_at FROM memory_edges"
_SELECT_OUT = f"{_SELECT_ALL} WHERE src_id = ?"
_SELECT_IN = f"{_SELECT_ALL} WHERE dst_id = ?"
_COUNT = "SELECT COUNT(*) FROM memory_edges"


def _edge_to_row(edge: MemoryEdge) -> tuple[str, str, str, float, str, str]:
    return (
        str(edge.src_id),
        str(edge.dst_id),
        edge.relation,
        edge.confidence,
        edge.source,
        edge.created_at.isoformat(),
    )


def _row_to_edge(row: aiosqlite.Row) -> MemoryEdge:
    return MemoryEdge(
        src_id=UUID(row["src_id"]),
        dst_id=UUID(row["dst_id"]),
        relation=row["relation"],
        confidence=row["confidence"],
        source=row["source"],
        created_at=row["created_at"],
    )


class EdgeStore:
    """Async CRUD over ``memory_edges``. Composes a :class:`SQLiteAdapter`."""

    def __init__(self, sqlite: SQLiteAdapter) -> None:
        self._sqlite = sqlite

    async def connect(self) -> None:
        """Ensure the underlying store (and its schema, incl. ``memory_edges``)."""
        await self._sqlite.connect()

    async def aclose(self) -> None:
        await self._sqlite.aclose()

    @property
    def _conn(self) -> aiosqlite.Connection:
        return self._sqlite.connection

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert(self, edges: Iterable[MemoryEdge]) -> int:
        """Insert or replace ``edges`` (keyed on ``src_id, dst_id, relation``).

        Returns the number of edge rows written.
        """
        await self.connect()
        rows = [_edge_to_row(e) for e in edges]
        if not rows:
            return 0

        try:
            await self._conn.executemany(_INSERT, rows)
            await self._conn.commit()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"edge upsert failed: {exc}") from exc

        return len(rows)

    async def replace_source(self, source: str, edges: Iterable[MemoryEdge]) -> int:
        """Atomically replace every edge tagged ``source`` with ``edges``.

        Delete-then-insert in one transaction: a producer owns its ``source`` and
        rewrites that whole set each pass, so removed or re-pointed edges disappear
        without any per-edge diffing. Returns the number of edges written.
        """
        await self.connect()
        rows = [_edge_to_row(e) for e in edges]

        try:
            await self._conn.execute("BEGIN")
            await self._conn.execute(_DELETE_BY_SOURCE, (source,))
            if rows:
                await self._conn.executemany(_INSERT, rows)
            await self._conn.commit()
        except aiosqlite.Error as exc:
            await self._conn.rollback()
            raise MemoryStorageError(f"edge replace_source({source!r}) failed: {exc}") from exc

        return len(rows)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def outgoing(self, node_id: UUID) -> list[MemoryEdge]:
        """Edges pointing out of ``node_id`` (its references)."""
        return await self._query(_SELECT_OUT, (str(node_id),))

    async def incoming(self, node_id: UUID) -> list[MemoryEdge]:
        """Edges pointing into ``node_id`` (its backlinks)."""
        return await self._query(_SELECT_IN, (str(node_id),))

    async def neighbors(self, node_id: UUID) -> list[UUID]:
        """Distinct node ids adjacent to ``node_id`` in either direction.

        The seed→expand step of graph-aware retrieval: given a node, the ids of
        everything one hop away, deduped and order-stable (outgoing then incoming).
        """
        seen: set[UUID] = set()
        order: list[UUID] = []

        for edge in await self.outgoing(node_id):
            if edge.dst_id not in seen:
                seen.add(edge.dst_id)
                order.append(edge.dst_id)

        for edge in await self.incoming(node_id):
            if edge.src_id not in seen:
                seen.add(edge.src_id)
                order.append(edge.src_id)

        return order

    async def all(self) -> list[MemoryEdge]:
        """Every edge — for inspection and tests."""
        return await self._query(_SELECT_ALL, ())

    async def count(self) -> int:
        await self.connect()

        try:
            row = await (await self._conn.execute(_COUNT)).fetchone()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"edge count failed: {exc}") from exc
        return int(row[0]) if row is not None else 0

    async def _query(self, sql: str, params: tuple[str, ...]) -> list[MemoryEdge]:
        await self.connect()

        try:
            rows = await (await self._conn.execute(sql, params)).fetchall()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"edge query failed: {exc}") from exc
        try:
            return [_row_to_edge(row) for row in rows]
        except Exception as exc:
            raise MemoryStorageError(f"failed to decode stored edge row: {exc}") from exc
