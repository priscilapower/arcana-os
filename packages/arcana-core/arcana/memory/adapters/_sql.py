"""Centralized SQL + row<->model mapping for SQLiteAdapter.

All dialect-specific SQL lives here, isolated from adapter logic. The portability
seam for "use a different database" is the ``MemoryAdapter`` Protocol itself — a
future ``PostgresAdapter`` is a sibling class. When that lands, this is the one
module it forks (``?`` -> ``$1`` params, ``INTEGER`` bools -> ``BOOLEAN``,
``TEXT`` JSON -> ``JSONB``, ``ON CONFLICT`` upsert syntax). Keeping every statement
here makes that a contained, mechanical change rather than a hunt through logic.
"""

import json
from typing import Any

from arcana.types import MemoryEntry, MemoryQuery

# Canonical column order — the single source of truth shared by INSERT and SELECT
# so the two can never drift. Matches migration v1 in ``migrations.py``.
COLUMNS: tuple[str, ...] = (
    "id",
    "agent_id",
    "type",
    "content",
    "scope",
    "pool_name",
    "importance",
    "pinned",
    "is_consolidated",
    "consolidated_from",
    "confidence",
    "confidence_source",
    "has_conflict",
    "conflict_id",
    "embedding",
    "source_session_id",
    "tags",
    "created_at",
    "last_accessed_at",
    "access_count",
)

_COL_LIST = ", ".join(COLUMNS)
_PLACEHOLDERS = ", ".join("?" for _ in COLUMNS)
# Upsert: keyed on the primary key, every non-id column is overwritten on conflict.
_UPDATE_SET = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "id")

UPSERT = (
    f"INSERT INTO memory_entries ({_COL_LIST}) VALUES ({_PLACEHOLDERS}) ON CONFLICT(id) DO UPDATE SET {_UPDATE_SET}"
)

SELECT_BASE = f"SELECT {_COL_LIST} FROM memory_entries"

# Pinned entries first, then most important, then most recently touched.
ORDER_BY = " ORDER BY pinned DESC, importance DESC, last_accessed_at DESC"


def touch_sql(n: int) -> str:
    """UPDATE that bumps access tracking for ``n`` ids (one ``?`` per id)."""
    marks = ", ".join("?" for _ in range(n))
    return f"UPDATE memory_entries SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({marks})"


# --------------------------------------------------------------------------
# Row <-> model mapping
# --------------------------------------------------------------------------


def entry_to_row(entry: MemoryEntry) -> list[Any]:
    """Flatten a MemoryEntry into a positional row matching ``COLUMNS``."""
    return [
        str(entry.id),
        str(entry.agent_id),
        entry.type.value,
        entry.content,
        entry.scope.value,
        entry.pool_name,
        entry.importance,
        int(entry.pinned),
        int(entry.is_consolidated),
        json.dumps([str(u) for u in entry.consolidated_from]),
        entry.confidence,
        entry.confidence_source.value,
        int(entry.has_conflict),
        str(entry.conflict_id) if entry.conflict_id is not None else None,
        json.dumps(entry.embedding) if entry.embedding is not None else None,
        str(entry.source_session_id) if entry.source_session_id is not None else None,
        json.dumps(entry.tags),
        entry.created_at.isoformat(),
        entry.last_accessed_at.isoformat(),
        entry.access_count,
    ]


def row_to_entry(row: Any) -> MemoryEntry:
    """Rebuild a MemoryEntry from a DB row (sqlite3.Row or sequence).

    JSON columns are decoded here; Pydantic coerces UUID/datetime strings and
    enum values during ``model_validate``.
    """
    # Rows come back as sqlite3.Row (positional-indexable); index by COLUMNS order.
    r: dict[str, Any] = {col: row[idx] for idx, col in enumerate(COLUMNS)}
    return MemoryEntry.model_validate(
        {
            "id": r["id"],
            "agent_id": r["agent_id"],
            "type": r["type"],
            "content": r["content"],
            "scope": r["scope"],
            "pool_name": r["pool_name"],
            "importance": r["importance"],
            "pinned": bool(r["pinned"]),
            "is_consolidated": bool(r["is_consolidated"]),
            "consolidated_from": json.loads(r["consolidated_from"]),
            "confidence": r["confidence"],
            "confidence_source": r["confidence_source"],
            "has_conflict": bool(r["has_conflict"]),
            "conflict_id": r["conflict_id"],
            "embedding": json.loads(r["embedding"]) if r["embedding"] is not None else None,
            "source_session_id": r["source_session_id"],
            "tags": json.loads(r["tags"]),
            "created_at": r["created_at"],
            "last_accessed_at": r["last_accessed_at"],
            "access_count": r["access_count"],
        }
    )


# --------------------------------------------------------------------------
# Dynamic WHERE builder for search()
# --------------------------------------------------------------------------


def build_where(query: MemoryQuery) -> tuple[str, list[Any]]:
    """Translate a MemoryQuery into a parameterized WHERE clause.

    A1 is filter-and-order only. ``query.text`` (the semantic query string) does
    not drive relevance here — vector ranking arrives with C1, keyword/BM25 with
    A2. ``query.keywords`` get a naive substring ``LIKE`` as a usable stopgap.
    ``include_archived`` is intentionally a no-op: MemoryEntry has no archival
    field yet (that originates with E4 pruning).
    """
    clauses: list[str] = []
    params: list[Any] = []

    if query.agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(str(query.agent_id))
    if query.type is not None:
        clauses.append("type = ?")
        params.append(query.type.value)
    if query.scope is not None:
        clauses.append("scope = ?")
        params.append(query.scope.value)
    if query.pool_name is not None:
        clauses.append("pool_name = ?")
        params.append(query.pool_name)

    # Thresholds default to 0.0 (match-all) but are always bound for clarity.
    clauses.append("importance >= ?")
    params.append(query.min_importance)
    clauses.append("confidence >= ?")
    params.append(query.min_confidence)

    if query.time_from is not None:
        clauses.append("created_at >= ?")
        params.append(query.time_from.isoformat())
    if query.time_to is not None:
        clauses.append("created_at <= ?")
        params.append(query.time_to.isoformat())

    if not query.include_conflicted:
        clauses.append("has_conflict = 0")

    for kw in query.keywords:
        clauses.append("content LIKE ?")
        params.append(f"%{kw}%")

    # tags stored as a JSON array string; naive contains is adequate at Phase 1b
    # scale. Normalize to a side table if tag filtering ever gets hot.
    for tag in query.tags:
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
