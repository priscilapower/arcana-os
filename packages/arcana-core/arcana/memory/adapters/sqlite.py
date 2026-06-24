"""SQLiteAdapter — the first concrete ``MemoryAdapter``.

Single-file async SQLite backend over ``aiosqlite``. Scope-aware read/write of
``MemoryEntry`` with importance-based promotion to GLOBAL. One adapter instance
owns one ``.db`` file; cross-tier topology (which file is private vs. a shared
pool vs. global) is decided one layer up by ``MemoryFederation``.
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

import aiosqlite

from arcana.memory.adapters import _sql
from arcana.memory.errors import MemoryStorageError
from arcana.memory.migrations import migrate_to_latest
from arcana.observability import MemoryReadEvent, MemoryWriteEvent, get_audit_log
from arcana.types import MemoryEntry, MemoryQuery, MemoryScope


def _default_base() -> Path:
    return Path.home() / ".arcana" / "agents"


class SQLiteAdapter:
    """Async SQLite memory backend. Implements the ``MemoryAdapter`` protocol."""

    #: Mirrors ``MemoryEntry.should_promote_to_global`` — kept as a class constant
    #: for callers that want to reason about the threshold without an entry.
    PROMOTION_THRESHOLD: float = 0.9

    def __init__(
        self,
        db_path: Path,
        *,
        global_store: SQLiteAdapter | None = None,
        refresh_on_access: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        self._global_store = global_store
        self._refresh_on_access = refresh_on_access
        self._conn: aiosqlite.Connection | None = None

    @classmethod
    def for_agent(
        cls,
        agent_id: UUID,
        base_dir: Path | None = None,
        **kwargs: object,
    ) -> SQLiteAdapter:
        """Build an adapter at ``~/.arcana/agents/{agent_id}/memory.db``.

        Mirrors ``SessionManager``'s path convention so an agent's memory lives
        beside its sessions.
        """
        base = base_dir or _default_base()
        return cls(base / str(agent_id) / "memory.db", **kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the connection, set pragmas, and migrate to the latest schema.

        Idempotent — safe to call repeatedly. ``search``/``write`` lazily call
        this, so explicit ``connect()`` is optional.
        """
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)
        conn.row_factory = aiosqlite.Row
        # WAL: concurrent readers alongside a single writer. busy_timeout: wait
        # rather than fail on transient lock contention.
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        await self._assert_fts5(conn)
        await migrate_to_latest(conn)
        self._conn = conn
        if self._global_store is not None:
            await self._global_store.connect()

    async def aclose(self) -> None:
        """Close the connection. Safe to call more than once."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _ensure(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.connect()
        assert self._conn is not None  # noqa: S101 — narrow type after connect
        return self._conn

    @staticmethod
    async def _assert_fts5(conn: aiosqlite.Connection) -> None:
        """Fail loudly at connect if this SQLite build lacks FTS5.

        Keyword search depends on FTS5, so we verify the capability up front
        rather than let the v2 migration explode with a cryptic
        ``CREATE VIRTUAL TABLE`` error, or silently degrade at query time.
        """
        row = await (
            await conn.execute("SELECT 1 FROM pragma_compile_options WHERE compile_options = 'ENABLE_FTS5'")
        ).fetchone()
        if row is None:
            raise MemoryStorageError(
                "This SQLite build lacks FTS5; keyword memory search is unavailable. "
                "Use a Python built against SQLite with FTS5, or install `pysqlite3-binary`."
            )

    # ------------------------------------------------------------------
    # MemoryAdapter protocol
    # ------------------------------------------------------------------

    async def write(self, entry: MemoryEntry) -> None:
        """Upsert one entry (keyed on ``id``), then promote to GLOBAL if eligible."""
        conn = await self._ensure()
        try:
            await conn.execute(_sql.UPSERT, _sql.entry_to_row(entry))
            await conn.commit()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"write failed for entry {entry.id}: {exc}") from exc

        # Importance-based promotion. The entry's own rule gates on scope == PRIVATE,
        # so the GLOBAL copy can never re-promote (no recursion). Same id keeps the
        # global write idempotent across re-writes. Promotion is a no-op without a
        # configured global store — a higher layer wires that in.
        if self._global_store is not None and entry.should_promote_to_global:
            promoted = entry.model_copy(update={"scope": MemoryScope.GLOBAL, "pool_name": None})
            await self._global_store.write(promoted)

        self._emit_write(entry)

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Return entries matching ``query``.

        When the query carries usable text, results are ranked by FTS5 BM25
        relevance (keyword search). Otherwise it's filter-and-order:
        pinned → importance → recency. Any text query — whatever its
        ``retrieval_mode`` — is currently served by the keyword path.
        """
        conn = await self._ensure()
        started = time.perf_counter()

        match = _sql.to_match_query(query.text) if query.text else None
        sql, params = _sql.keyword_search(query, match) if match is not None else _sql.filter_search(query)

        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"search failed: {exc}") from exc

        # Decode/validation failures (e.g. corrupt JSON in a list column) are
        # translated too, so callers only ever see MemoryStorageError.
        try:
            entries = [_sql.row_to_entry(row) for row in rows]
        except Exception as exc:
            raise MemoryStorageError(f"failed to decode stored memory row: {exc}") from exc

        # Access tracking drives decay refresh (DecayProfile.refresh_on_access).
        # Trade-off: every read becomes a small write; batched into one UPDATE and
        # toggleable per-instance.
        if self._refresh_on_access and entries:
            await self._touch([e.id for e in entries])
            for entry in entries:
                entry.bump_access()

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._emit_read(query, len(entries), elapsed_ms)
        return entries

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _touch(self, ids: list[UUID]) -> None:
        conn = await self._ensure()
        now_iso = _now_iso()
        params: list[object] = [now_iso, *[str(i) for i in ids]]
        try:
            await conn.execute(_sql.touch_sql(len(ids)), params)
            await conn.commit()
        except aiosqlite.Error as exc:
            raise MemoryStorageError(f"access-tracking update failed: {exc}") from exc

    def _emit_write(self, entry: MemoryEntry) -> None:
        """Best-effort audit event. Never let observability break the memory path."""
        try:
            audit = get_audit_log()
            if audit is not None:
                audit.append(
                    MemoryWriteEvent(
                        session_id=str(entry.source_session_id or ""),
                        agent_id=str(entry.agent_id),
                        memory_type=entry.type.value,
                        importance=entry.importance,
                    )
                )
        except Exception:
            pass

    def _emit_read(self, query: MemoryQuery, count: int, latency_ms: int) -> None:
        try:
            audit = get_audit_log()
            if audit is not None:
                audit.append(
                    MemoryReadEvent(
                        session_id="",
                        agent_id=str(query.agent_id or ""),
                        query_text=query.text or "",
                        results_count=count,
                        latency_ms=latency_ms,
                    )
                )
        except Exception:
            pass


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
