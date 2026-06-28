"""Centralized SQL + row<->model mapping for SQLiteAdapter.

All dialect-specific SQL lives here, isolated from adapter logic. The portability
seam for "use a different database" is the ``MemoryAdapter`` Protocol itself — a
future ``PostgresAdapter`` is a sibling class. When that lands, this is the one
module it forks (``?`` -> ``$1`` params, ``INTEGER`` bools -> ``BOOLEAN``,
``TEXT`` JSON -> ``JSONB``, ``ON CONFLICT`` upsert syntax). Keeping every statement
here makes that a contained, mechanical change rather than a hunt through logic.
"""

import json
import re
from typing import Any

from arcana.types import EmbeddingMeta, MemoryEntry, MemoryQuery

# Canonical column order — the single source of truth shared by INSERT and SELECT
# so the two can never drift. Matches migration v1 in ``migrations/versions/``.
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
# Keyword search (FTS5) — query-string sanitization
# --------------------------------------------------------------------------

# Characters that carry meaning in the FTS5 MATCH grammar. Stripping them (and
# quoting each surviving token) guarantees arbitrary user text can never produce
# a syntax error or inject MATCH operators.
_FTS_META = re.compile(r'["()*:^\-]')


def to_match_query(text: str) -> str | None:
    """Translate free user text into a safe FTS5 ``MATCH`` expression.

    Each token is stripped of FTS5 operator characters and wrapped as a quoted
    literal, then OR-joined. Quoting also neutralizes bareword keywords (AND, OR,
    NEAR) so they match literally rather than as operators. OR favours recall —
    the right bias for a keyword fallback — while ``bm25()`` still ranks the
    strongest matches to the top. Returns ``None`` when nothing searchable
    remains (empty / punctuation-only input), signalling the caller to fall back
    to plain filter-and-order rather than run an empty match.
    """
    tokens = [t for t in _FTS_META.sub(" ", text).split() if t]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


# --------------------------------------------------------------------------
# Dynamic WHERE builder + search-SQL assembly
# --------------------------------------------------------------------------


def _where_clauses(query: MemoryQuery, alias: str = "") -> tuple[list[str], list[Any]]:
    """Build parameterized WHERE clauses for ``query``.

    ``alias`` qualifies every column (e.g. ``m.scope``) so the same filters can
    sit on the bare ``memory_entries`` table or on the aliased side of the FTS5
    join. ``query.text`` does not appear here — relevance ranking is the search
    builders' job (FTS5 ``MATCH`` for keyword/BM25). ``query.keywords`` keep their
    naive substring ``LIKE`` as a complementary filter. ``include_archived`` is
    intentionally a no-op: MemoryEntry has no archival field yet.
    """
    p = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: list[Any] = []

    if query.agent_id is not None:
        clauses.append(f"{p}agent_id = ?")
        params.append(str(query.agent_id))
    if query.type is not None:
        clauses.append(f"{p}type = ?")
        params.append(query.type.value)
    if query.scope is not None:
        clauses.append(f"{p}scope = ?")
        params.append(query.scope.value)
    if query.pool_name is not None:
        clauses.append(f"{p}pool_name = ?")
        params.append(query.pool_name)

    # Thresholds default to 0.0 (match-all) but are always bound for clarity.
    clauses.append(f"{p}importance >= ?")
    params.append(query.min_importance)
    clauses.append(f"{p}confidence >= ?")
    params.append(query.min_confidence)

    if query.time_from is not None:
        clauses.append(f"{p}created_at >= ?")
        params.append(query.time_from.isoformat())
    if query.time_to is not None:
        clauses.append(f"{p}created_at <= ?")
        params.append(query.time_to.isoformat())

    if not query.include_conflicted:
        clauses.append(f"{p}has_conflict = 0")

    for kw in query.keywords:
        clauses.append(f"{p}content LIKE ?")
        params.append(f"%{kw}%")

    # tags stored as a JSON array string; naive contains is adequate at current
    # scale. Normalize to a side table if tag filtering ever gets hot.
    for tag in query.tags:
        clauses.append(f"{p}tags LIKE ?")
        params.append(f'%"{tag}"%')

    return clauses, params


def build_where(query: MemoryQuery) -> tuple[str, list[Any]]:
    """Public, unaliased WHERE fragment (``" WHERE ..."`` or ``""``)."""
    clauses, params = _where_clauses(query)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def filter_search(query: MemoryQuery) -> tuple[str, list[Any]]:
    """Filter-and-order SELECT: no text relevance, ordered pinned→importance→recency.

    Used when a query carries no usable text (``to_match_query`` returned None).
    """
    where, params = build_where(query)
    sql = f"{SELECT_BASE}{where}{ORDER_BY} LIMIT ?"
    params.append(query.limit)
    return sql, params


def keyword_search(query: MemoryQuery, match: str) -> tuple[str, list[Any]]:
    """BM25-ranked SELECT over the FTS5 index, with the same metadata filters.

    The FTS table supplies the lexical ranking; ``memory_entries`` (aliased ``m``)
    supplies the row data and filters. ``bm25()`` is selected as a trailing
    ``_score`` column — ``row_to_entry`` ignores it (it reads only ``COLUMNS`` by
    position), but it's selected so callers that rank or fuse scores can read the
    raw value. Lower bm25 is a better match, so ``ORDER BY _score`` ascending puts
    the strongest hits first.
    """
    clauses, params = _where_clauses(query, alias="m")
    where = " AND ".join(["memory_entries_fts MATCH ?", *clauses])
    cols = ", ".join(f"m.{c}" for c in COLUMNS)
    sql = (
        f"SELECT {cols}, bm25(memory_entries_fts) AS _score "
        "FROM memory_entries_fts "
        "JOIN memory_entries m ON m.id = memory_entries_fts.entry_id "
        f"WHERE {where} ORDER BY _score LIMIT ?"
    )
    return sql, [match, *params, query.limit]


def bm25_candidates(query: MemoryQuery, match: str, k: int) -> tuple[str, list[Any]]:
    """``entry_id`` + raw BM25 score for the top-``k`` keyword matches.

    The fusion sibling of ``keyword_search``: returns only ids and raw ``bm25()``
    scores (no row hydration) so the hybrid path can union these candidates with
    the vector leg before a single filtered fetch. Same metadata filters; lower
    bm25 is a better match.
    """
    clauses, params = _where_clauses(query, alias="m")
    where = " AND ".join(["memory_entries_fts MATCH ?", *clauses])
    sql = (
        "SELECT m.id, bm25(memory_entries_fts) AS _score "
        "FROM memory_entries_fts "
        "JOIN memory_entries m ON m.id = memory_entries_fts.entry_id "
        f"WHERE {where} ORDER BY _score LIMIT ?"
    )
    return sql, [match, *params, k]


def fetch_by_ids(query: MemoryQuery, ids: list[str]) -> tuple[str, list[Any]]:
    """SELECT full rows for a set of ids, with the query's metadata filters applied.

    The vector path resolves candidate ids from the ``vec0`` KNN index, then
    rehydrates them here — the same scope/importance/confidence/time/tag filters
    that gate keyword search also gate semantic results. No ``ORDER BY``/``LIMIT``:
    the caller re-orders by KNN distance and truncates, since SQL has no notion of
    the vector ranking.
    """
    clauses, params = _where_clauses(query)
    marks = ", ".join("?" for _ in ids)
    conds = [f"id IN ({marks})", *clauses]
    sql = f"{SELECT_BASE} WHERE {' AND '.join(conds)}"
    return sql, [*ids, *params]


# --------------------------------------------------------------------------
# Vector index (sqlite-vec) + per-database embedding-model pin
# --------------------------------------------------------------------------

# vec0 virtual table holding one unit-length embedding per memory id. Lives in
# the same memory.db as ``memory_entries`` so row + vector writes share one
# connection and stay consistent (the pattern the FTS5 triggers already follow).
VEC_TABLE = "memory_vectors"


def vec_table_ddl(dims: int) -> str:
    """DDL for the vec0 index, sized to ``dims`` and using cosine distance.

    Dimension is fixed at CREATE time and determined at runtime by the resolved
    embedder, so this is created lazily on the first embedded write — not as a
    static ``PRAGMA user_version`` migration. ``dims`` is an int we control
    (``EmbeddingAdapter.dimensions``), never user input, so interpolation is safe.
    """
    return (
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE} USING vec0("
        "entry_id TEXT PRIMARY KEY, "
        f"embedding float[{int(dims)}] distance_metric=cosine"
        ")"
    )


# vec0 does not honour ``INSERT OR REPLACE`` upsert semantics on the declared
# primary key, so re-indexing is delete-then-insert — the same shape the FTS5
# sync triggers use.
VEC_DELETE = f"DELETE FROM {VEC_TABLE} WHERE entry_id = ?"
VEC_INSERT = f"INSERT INTO {VEC_TABLE}(entry_id, embedding) VALUES (?, ?)"

# vec0 KNN: MATCH supplies the query vector, ``k`` bounds the neighbour count.
# Lower distance = closer; ORDER BY distance puts the strongest matches first.
VEC_KNN = f"SELECT entry_id, distance FROM {VEC_TABLE} WHERE embedding MATCH ? AND k = ? ORDER BY distance"

# Single-row ``embedding_meta`` table (migration v3): the model a DB is pinned to.
READ_EMBEDDING_META = "SELECT model_name, dimensions, first_used_at, entry_count FROM embedding_meta LIMIT 1"
INSERT_EMBEDDING_META = (
    "INSERT INTO embedding_meta (model_name, dimensions, first_used_at, entry_count) VALUES (?, ?, ?, ?)"
)
BUMP_ENTRY_COUNT = "UPDATE embedding_meta SET entry_count = entry_count + 1"


def meta_row_to_model(row: Any) -> EmbeddingMeta:
    """Rebuild an ``EmbeddingMeta`` from an ``embedding_meta`` row."""
    return EmbeddingMeta.model_validate(
        {
            "model_name": row[0],
            "dimensions": row[1],
            "first_used_at": row[2],
            "entry_count": row[3],
        }
    )
