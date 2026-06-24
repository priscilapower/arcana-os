# Memory

`arcana.memory` holds the concrete storage backends behind the
[`MemoryAdapter`](types.md#arcana.types.memory.MemoryAdapter) protocol. The
protocol is the seam: agents read and write through it and never learn which
backend is underneath, so swapping stores is a matter of supplying a different
adapter.

`SQLiteAdapter` is the first backend — a single-file, async store built on
[`aiosqlite`](https://pypi.org/project/aiosqlite/). One adapter instance owns one
`.db` file; an agent's private memory lives at
`~/.arcana/agents/{agent_id}/memory.db`.

```python
from uuid import uuid4

from arcana.memory import SQLiteAdapter
from arcana.types import MemoryEntry, MemoryQuery, MemoryType

agent_id = uuid4()
memory = SQLiteAdapter.for_agent(agent_id)
await memory.connect()                       # opens the file, runs migrations

await memory.write(
    MemoryEntry(
        agent_id=agent_id,
        type=MemoryType.SEMANTIC,
        content="The user prefers metric units.",
        importance=0.8,
    )
)

results = await memory.search(MemoryQuery(agent_id=agent_id, limit=5))
await memory.aclose()
```

## Keyword search

When a query carries `text`, `search()` ranks results by full-text relevance
(BM25) using a SQLite FTS5 index over each entry's `content` and `tags`. The
usual metadata filters — scope, type, confidence, time range, conflict
exclusion — still apply on top of the text match. A query with no text falls
back to filter-and-order: pinned first, then importance, then recency.

```python
from arcana.types import MemoryQuery, RetrievalMode

results = await memory.search(
    MemoryQuery(
        agent_id=agent_id,
        text="metric units preference",
        retrieval_mode=RetrievalMode.keyword,
        limit=5,
    )
)
```

Arbitrary user text is safe to pass directly: it is sanitized into a valid FTS5
`MATCH` expression (operator characters stripped, tokens quoted and OR-joined),
so it can never raise a syntax error. The index is kept in lockstep with the
entries table by database triggers, so writes, upserts, and deletes need no
extra bookkeeping.

FTS5 must be compiled into SQLite — `connect()` raises `MemoryStorageError` if
the build lacks it.

## Importance-based promotion

When an adapter is constructed with a `global_store`, writing a `PRIVATE` entry
whose `importance >= 0.9` also copies it into the global store as a `GLOBAL`
entry — the mechanism behind "all agents read; The World writes". Without a
`global_store`, promotion is a no-op.

```python
global_store = SQLiteAdapter(global_db_path)
agent_memory = SQLiteAdapter.for_agent(agent_id, global_store=global_store)
```

## Schema migrations

Schema is versioned with SQLite's built-in `PRAGMA user_version` — deliberately
**no Alembic or SQLAlchemy**, to keep `arcana-core` dependency-light.
`connect()` brings the database to the latest version automatically.

The `arcana.memory.migrations` package separates the runner (`runner.py`, the
forward-only engine that applies migrations) from the definitions
(`versions/`, one module per schema version). Definitions are append-only:
never edit a shipped migration — add a new `versions/vNNN_*.py` module and
register it. Each migration's DDL and its `user_version` bump share a
transaction, so a partial failure rolls back atomically.

## What `SQLiteAdapter` does *not* do yet

`search()` covers keyword (BM25) relevance and metadata filtering. Vector
similarity (the `embedding` column is stored but not searched), hybrid score
fusion, and cross-tier federation arrive in later building blocks of Memory
Federation.

## Adapter

::: arcana.memory.adapters.sqlite.SQLiteAdapter

## Migrations

::: arcana.memory.migrations.migrate_to_latest

::: arcana.memory.migrations.latest_version

## Errors

::: arcana.memory.errors.MemoryError

::: arcana.memory.errors.MemoryStorageError

::: arcana.memory.errors.MemoryNotConnectedError
