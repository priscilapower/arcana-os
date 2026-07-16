"""SQLiteAdapter — the first concrete ``MemoryAdapter``.

Single-file async SQLite backend over ``aiosqlite``. Scope-aware read/write of
``MemoryEntry`` with importance-based promotion to GLOBAL. One adapter instance
owns one ``.db`` file; cross-tier topology (which file is private vs. a shared
pool vs. global) is decided one layer up by ``MemoryFederation``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite

from arcana.memory.adapters import _sql
from arcana.memory.errors import MemoryCorruptError, MemoryNotConnectedError, MemoryStorageError
from arcana.memory.migrations import migrate_to_latest
from arcana.observability import MemoryPruneEvent, MemoryReadEvent, MemoryWriteEvent, get_audit_log
from arcana.types import (
    AdapterHealth,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    PruneMode,
    PrunePolicy,
    PruneReport,
)


def _default_base() -> Path:
    return Path.home() / ".arcana" / "agents"


#: Substrings SQLite uses to report a physically damaged or non-database file.
#: A driver error carrying any of these is corruption, not transient contention.
#: Includes ``vtable constructor failed`` — how damaged FTS5 shadow tables surface
#: on an existing store, where FTS5 availability is already asserted at connect.
_CORRUPTION_MARKERS = (
    "malformed",
    "not a database",
    "disk image",
    "corrupt",
    "vtable constructor failed",
)


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
        quick_check_on_open: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        self._global_store = global_store
        self._refresh_on_access = refresh_on_access
        # PRIVATE stores open once per session, so a cheap integrity check at open
        # quarantines a corrupt agent before it runs. SHARED/GLOBAL stores open
        # widely and rely on detect-on-read instead; wiring disables this for them.
        self._quick_check_on_open = quick_check_on_open
        self._conn: aiosqlite.Connection | None = None
        #: Set to the integrity-failure detail once corruption is seen. Latches:
        #: a quarantined store stays quarantined for this adapter's lifetime.
        self._corrupt: str | None = None

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
        if self._corrupt is not None:
            raise MemoryCorruptError(f"store at {self._db_path} is quarantined: {self._corrupt}")
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._db_path)

        try:
            conn.row_factory = aiosqlite.Row
            # WAL: concurrent readers alongside a single writer. busy_timeout: wait
            # rather than fail on transient lock contention.
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")
            await self._assert_fts5(conn)
            if self._quick_check_on_open:
                await self._run_quick_check(conn)
            await migrate_to_latest(conn)
        except aiosqlite.Error as exc:
            # A driver error during open on a file that should already be a valid
            # database is corruption territory — translate before it escapes raw.
            await conn.close()
            raise self._translate_sqlite_error(exc, f"failed to open store at {self._db_path}") from exc
        except MemoryCorruptError:
            await conn.close()
            raise
        self._conn = conn
        if self._global_store is not None:
            await self._global_store.connect()

    async def _run_quick_check(self, conn: aiosqlite.Connection) -> None:
        """Run ``PRAGMA quick_check`` and quarantine the store if it is damaged.

        ``quick_check`` skips the expensive index cross-checks of a full
        ``integrity_check`` — milliseconds on the small per-agent stores — while
        still catching a malformed page image. A non-``ok`` result latches
        ``_corrupt`` and raises ``MemoryCorruptError``.
        """
        row = await (await conn.execute("PRAGMA quick_check")).fetchone()
        result = str(row[0]) if row is not None else "no result"
        if result != "ok":
            self._corrupt = result
            raise MemoryCorruptError(f"integrity check failed for {self._db_path}: {result}")

    def _translate_sqlite_error(self, exc: aiosqlite.Error, prefix: str) -> MemoryStorageError:
        """Map a driver error to the memory taxonomy, flagging corruption.

        A malformed-image / not-a-database error latches ``_corrupt`` and becomes
        ``MemoryCorruptError`` (which the resilience layer quarantines for the
        session); anything else stays a plain ``MemoryStorageError``.
        """
        text = str(exc).lower()
        if any(marker in text for marker in _CORRUPTION_MARKERS):
            self._corrupt = str(exc)
            return MemoryCorruptError(f"{prefix}: {exc}")
        return MemoryStorageError(f"{prefix}: {exc}")

    async def aclose(self) -> None:
        """Close the connection. Safe to call more than once."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _close_conn_quietly(self) -> None:
        """Drop the underlying connection, swallowing any close error.

        Called when a read/write surfaces corruption: the store is quarantined
        for the rest of its lifetime (``_corrupt`` latches), so keeping the
        aiosqlite connection open only leaks its worker thread — a non-daemon
        thread that blocks interpreter shutdown. Releasing it lets the process
        exit cleanly while the ``_corrupt`` latch keeps subsequent calls failing.
        """
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 — we are already failing; a close error is moot
                pass

    async def _fail_translated(self, exc: aiosqlite.Error, prefix: str) -> MemoryStorageError:
        """Translate a driver error and, if it is corruption, release the connection."""
        err = self._translate_sqlite_error(exc, prefix)
        if isinstance(err, MemoryCorruptError):
            await self._close_conn_quietly()
        return err

    async def health_check(self) -> AdapterHealth:
        """Probe the backend with a trivial query. Never raises.

        Ensures the connection (which validates the store), then runs
        ``SELECT 1``. Any failure — unconnectable, corrupt, or FTS5-less —
        reports unhealthy so the resilience layer can gate on it as its
        half-open recovery probe without exception handling.
        """
        adapter_id = str(self._db_path)
        if self._corrupt is not None:
            return AdapterHealth(adapter_id=adapter_id, healthy=False, message=f"corrupt: {self._corrupt}")

        try:
            conn = await self._ensure()
            await conn.execute("SELECT 1")
            return AdapterHealth(adapter_id=adapter_id, healthy=True)
        except Exception as exc:  # noqa: BLE001 — a health probe must not raise
            return AdapterHealth(adapter_id=adapter_id, healthy=False, message=str(exc))

    async def _ensure(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.connect()
        assert self._conn is not None  # noqa: S101 — narrow type after connect
        return self._conn

    @property
    def connection(self) -> aiosqlite.Connection:
        """The live connection, for sibling adapters sharing this database.

        ``VectorAdapter`` layers a vec0 index onto the same ``memory.db`` and
        needs this exact connection so its vector writes sit in the same database
        as the rows. Raises if the adapter was never connected.
        """
        if self._conn is None:
            raise MemoryNotConnectedError("adapter is not connected; call connect() first")
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
            raise await self._fail_translated(exc, f"write failed for entry {entry.id}") from exc

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
            raise await self._fail_translated(exc, "search failed") from exc

        # Decode/validation failures (e.g. corrupt JSON in a list column) are
        # translated too, so callers only ever see MemoryStorageError.
        try:
            entries = [_sql.row_to_entry(row) for row in rows]
        except Exception as exc:
            raise MemoryStorageError(f"failed to decode stored memory row: {exc}") from exc

        await self.record_read(query, entries, started)
        return entries

    async def record_read(self, query: MemoryQuery, entries: list[MemoryEntry], started: float) -> None:
        """Post-read bookkeeping: refresh access tracking and emit the read event.

        Public so a sibling adapter (``VectorAdapter``) whose semantic path
        bypasses ``search`` shares the same access-refresh + audit behaviour.
        ``started`` is a ``time.perf_counter()`` stamp taken before the query ran.
        """
        await self._refresh_access(entries)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self._emit_read(query, len(entries), elapsed_ms)

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    async def prune(self, policy: PrunePolicy) -> PruneReport:
        """Remove low-value entries per ``policy``; pinned entries are never touched.

        ARCHIVE soft-deletes (sets ``archived``, hiding entries from search but
        keeping them recoverable); PURGE hard-deletes the row — and its vector,
        if a vec index exists. The importance floor and max-entries cap compose
        into a single victim set, removed in one committed transaction.
        """
        conn = await self._ensure()
        purge = policy.mode is PruneMode.PURGE
        try:
            row = await (await conn.execute(_sql.PRUNABLE_COUNT)).fetchone()
            scanned = int(row[0]) if row is not None else 0

            victims: set[str] = set()
            if policy.min_importance is not None:
                cursor = await conn.execute(
                    _sql.prune_below_importance_sql(include_archived=purge), [policy.min_importance]
                )
                victims.update(r[0] for r in await cursor.fetchall())
            if policy.max_entries is not None:
                cursor = await conn.execute(_sql.prune_over_cap_sql(), [policy.max_entries])
                victims.update(r[0] for r in await cursor.fetchall())

            ids = list(victims)
            archived = purged = 0
            if ids:
                if purge:
                    await conn.execute(_sql.delete_ids_sql(len(ids)), ids)
                    if await self._vec_table_exists(conn):
                        for vid in ids:
                            await conn.execute(_sql.VEC_DELETE, (vid,))
                    purged = len(ids)
                else:
                    await conn.execute(_sql.archive_ids_sql(len(ids)), ids)
                    archived = len(ids)
            await conn.commit()
        except aiosqlite.Error as exc:
            raise await self._fail_translated(exc, "prune failed") from exc

        report = PruneReport(scanned=scanned, archived=archived, purged=purged)
        self._emit_prune(report)
        return report

    @staticmethod
    async def _vec_table_exists(conn: aiosqlite.Connection) -> bool:
        row = await (await conn.execute(_sql.TABLE_EXISTS, (_sql.VEC_TABLE,))).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _refresh_access(self, entries: list[MemoryEntry]) -> None:
        """Bump access tracking for the read entries, driving decay refresh.

        Access tracking drives decay refresh (``DecayProfile.refresh_on_access``):
        the persisted row's ``last_accessed_at`` is reset to now so a *future*
        read observes a fresh clock. The returned entry keeps its read-time
        ``last_accessed_at`` on purpose — decay ranking downstream must see the
        entry's true age at *this* read, not a clock the read itself just reset,
        or nothing would ever age out of retrieval. Only ``access_count`` is
        bumped in place.

        Trade-off: every read becomes a small write; batched into one UPDATE and
        toggleable per-instance. Shared with ``VectorAdapter``, whose semantic
        path bypasses ``search`` but still needs the same refresh.
        """
        if self._refresh_on_access and entries:
            await self._touch([e.id for e in entries])
            for entry in entries:
                entry.access_count += 1

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

    def _emit_prune(self, report: PruneReport) -> None:
        """Best-effort audit event. The store can span agents, so agent_id is blank."""
        try:
            audit = get_audit_log()
            if audit is not None:
                audit.append(
                    MemoryPruneEvent(
                        agent_id="",
                        scanned=report.scanned,
                        archived=report.archived,
                        purged=report.purged,
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
    return datetime.now(UTC).isoformat()
