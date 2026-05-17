"""SQLiteAdapter — structured memory, always-on backbone."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import aiosqlite

from arcana.memory.adapters.base import MemoryAdapter
from arcana.types.memory import (
    AdapterCapabilities,
    AdapterHealth,
    MemoryEntry,
    MemoryQuery,
    MemoryType,
)


class SQLiteAdapter(MemoryAdapter):
    """
    Fast, reliable backbone. Stores structured facts, preferences,
    and session metadata. Always-on — every agent has one.
    """

    adapter_id = "sqlite"
    capabilities = AdapterCapabilities(
        supports_vector=False,
        supports_full_text=True,
        supports_tags=True,
        supports_time_range=True,
        is_writable=True,
        is_persistent=True,
    )

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._create_schema()

    async def _create_schema(self) -> None:
        assert self._db
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_entries (
                id          TEXT PRIMARY KEY,
                agent_id    TEXT NOT NULL,
                type        TEXT NOT NULL,
                content     TEXT NOT NULL,
                importance  REAL DEFAULT 0.5,
                tags        TEXT DEFAULT '[]',
                source_session_id TEXT,
                created_at  TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_agent_id
                ON memory_entries (agent_id);
            CREATE INDEX IF NOT EXISTS idx_type
                ON memory_entries (type);
            CREATE INDEX IF NOT EXISTS idx_importance
                ON memory_entries (importance);

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(id UNINDEXED, content, agent_id UNINDEXED);
        """)
        await self._db.commit()

    async def write(self, entry: MemoryEntry) -> str:
        assert self._db
        await self._db.execute(
            """
            INSERT OR REPLACE INTO memory_entries
                (id, agent_id, type, content, importance, tags,
                 source_session_id, created_at, last_accessed_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(entry.id),
                str(entry.agent_id),
                entry.type.value,
                entry.content,
                entry.importance,
                json.dumps(entry.tags),
                str(entry.source_session_id) if entry.source_session_id else None,
                entry.created_at.isoformat(),
                entry.last_accessed_at.isoformat(),
                entry.access_count,
            ),
        )
        # Keep FTS in sync
        await self._db.execute(
            "INSERT OR REPLACE INTO memory_fts (id, content, agent_id) VALUES (?, ?, ?)",
            (str(entry.id), entry.content, str(entry.agent_id)),
        )
        await self._db.commit()
        return str(entry.id)

    async def read(self, entry_id: str) -> MemoryEntry | None:
        assert self._db
        async with self._db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (entry_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    async def forget(self, entry_id: str) -> None:
        assert self._db
        await self._db.execute(
            "DELETE FROM memory_entries WHERE id = ?", (entry_id,)
        )
        await self._db.execute(
            "DELETE FROM memory_fts WHERE id = ?", (entry_id,)
        )
        await self._db.commit()

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        assert self._db
        conditions: list[str] = []
        params: list[object] = []

        if query.agent_id:
            conditions.append("agent_id = ?")
            params.append(str(query.agent_id))
        if query.type:
            conditions.append("type = ?")
            params.append(query.type.value)
        if query.min_importance > 0:
            conditions.append("importance >= ?")
            params.append(query.min_importance)
        if query.time_from:
            conditions.append("created_at >= ?")
            params.append(query.time_from.isoformat())
        if query.time_to:
            conditions.append("created_at <= ?")
            params.append(query.time_to.isoformat())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if query.keywords:
            # Use FTS for keyword search
            fts_query = " OR ".join(query.keywords)
            sql = f"""
                SELECT m.* FROM memory_entries m
                JOIN memory_fts f ON m.id = f.id
                WHERE f.content MATCH ?
                {('AND ' + ' AND '.join(conditions)) if conditions else ''}
                ORDER BY m.importance DESC, m.last_accessed_at DESC
                LIMIT ?
            """
            params_fts = [fts_query, *params, query.limit]
            async with self._db.execute(sql, params_fts) as cursor:
                rows = await cursor.fetchall()
        else:
            sql = f"""
                SELECT * FROM memory_entries {where}
                ORDER BY importance DESC, last_accessed_at DESC
                LIMIT ?
            """
            async with self._db.execute(sql, [*params, query.limit]) as cursor:
                rows = await cursor.fetchall()

        return [self._row_to_entry(row) for row in rows]

    async def health_check(self) -> AdapterHealth:
        try:
            assert self._db
            async with self._db.execute("SELECT 1") as cur:
                await cur.fetchone()
            return AdapterHealth(adapter_id=self.adapter_id, healthy=True)
        except Exception as e:
            return AdapterHealth(
                adapter_id=self.adapter_id, healthy=False, message=str(e)
            )

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    def _row_to_entry(self, row: aiosqlite.Row) -> MemoryEntry:
        from datetime import datetime
        return MemoryEntry(
            id=UUID(row["id"]),
            agent_id=UUID(row["agent_id"]),
            type=MemoryType(row["type"]),
            content=row["content"],
            importance=row["importance"],
            tags=json.loads(row["tags"]),
            source_session_id=UUID(row["source_session_id"])
            if row["source_session_id"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            last_accessed_at=datetime.fromisoformat(row["last_accessed_at"]),
            access_count=row["access_count"],
        )
