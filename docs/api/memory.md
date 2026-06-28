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

## Embedding gateway and model pinning

Vector search needs an embedder, and which one a database may use is **pinned**.
The first model to write an embedding is recorded in the database's
`embedding_meta` row (added by migration `v3`), and the database stays locked to
it. [`EmbeddingGateway.resolve()`](#embedding-gateway) turns that pin into the
adapter to embed with:

- **New database** (no pin yet) → the first healthy adapter, in priority order;
  the database is then pinned to it.
- **Pinned database** → the adapter for its exact model when healthy; otherwise a
  healthy adapter in the **same `model_family`** (interchangeable vectors);
  otherwise `None`.

A `None` result is the signal to fall back to keyword (FTS5) search. The gateway
never substitutes a model from a *different* family — that would compare vectors
across incompatible spaces and corrupt similarity scores with no error raised.

```python
from arcana.memory import EmbeddingGateway
from arcana.models.adapters.ollama_embedding import OllamaEmbeddingAdapter
from arcana.models.adapters.fastembed_embedding import FastEmbedEmbeddingAdapter

# Priority order: Ollama first, fastembed second.
gateway = EmbeddingGateway([OllamaEmbeddingAdapter(), FastEmbedEmbeddingAdapter()])

adapter = await gateway.resolve(db_meta)   # db_meta: EmbeddingMeta | None
if adapter is not None:
    vector = await adapter.embed("some text")
else:
    ...  # fall back to FTS5 keyword search
```

The gateway is pure resolution logic: it takes the database's
[`EmbeddingMeta`](types.md#arcana.types.memory.EmbeddingMeta) (or `None`) and
returns an adapter. Reading and writing the `embedding_meta` row belongs to the
vector backend that consumes the gateway — that backend is `VectorAdapter`,
below.

## Vector search (semantic)

`VectorAdapter` adds semantic search on top of a `SQLiteAdapter`. It *composes*
(does not subclass) the SQLite store and an `EmbeddingGateway`, storing
embeddings in a [sqlite-vec](https://github.com/asg017/sqlite-vec) `vec0` index
that lives in the same `memory.db` — so row and vector writes share one
connection and stay consistent. Vector storage is an optional extra:

```bash
pip install "arcana-os[vector]"
```

```python
from arcana.memory import EmbeddingGateway, SQLiteAdapter, VectorAdapter
from arcana.models.adapters.ollama_embedding import OllamaEmbeddingAdapter
from arcana.types import MemoryQuery, RetrievalMode

memory = VectorAdapter(
    SQLiteAdapter.for_agent(agent_id),
    EmbeddingGateway([OllamaEmbeddingAdapter()]),
)
await memory.connect()

await memory.write(entry)                    # embeds content, indexes the vector

results = await memory.search(
    MemoryQuery(
        agent_id=agent_id,
        text="units the user likes",
        retrieval_mode=RetrievalMode.semantic,
        limit=5,
    )
)
```

On `write()`, the adapter resolves an embedder through the gateway, embeds the
entry's `content` (when no vector is supplied), and stores it in the index. The
**first** embedded write both creates the dimension-sized index and pins the
database to the model (writing its `embedding_meta` row); vectors are
L2-normalized and the index ranks by cosine distance. A vector whose width
disagrees with the pin is refused with `MemoryStorageError` rather than
silently corrupting the index.

`search()` embeds the query, ranks by nearest-neighbour cosine distance, then
applies the same metadata filters as keyword search. It **falls back to FTS5
keyword search** (with a one-time warning) whenever no compatible embedder is
healthy — or when sqlite-vec is not installed — so memory stays usable without
the extra, just without semantic ranking. A query with no text uses the
filter-and-order path.

## Hybrid retrieval

`RetrievalMode.hybrid` fuses the vector and keyword legs into one ranking.
Because cosine distance and BM25 sit on different, incompatible scales, each leg
is converted to a higher-is-better relevance and **min-max normalized to
`[0, 1]` within the query's candidate pool**, then combined:

```text
finalScore = vector_weight × vNorm + bm25_weight × bm25Norm
```

Per-query normalization keeps either leg from dominating purely because of
scale. The weights are set on the adapter and normalized to sum 1, defaulting to
0.7 vector / 0.3 keyword:

```python
memory = VectorAdapter(sqlite, gateway, vector_weight=0.7, bm25_weight=0.3)

results = await memory.search(
    MemoryQuery(
        agent_id=agent_id,
        text="metric units",
        retrieval_mode=RetrievalMode.hybrid,
        limit=5,
    )
)
```

An entry surfaced by only one leg contributes 0 for the other. With no healthy
embedder, hybrid degrades to keyword-only (the BM25 leg alone).

## What the memory layer does *not* do yet

`search()` now covers keyword (BM25), semantic (vector), and hybrid retrieval
with metadata filtering, and the embedding gateway resolves and pins an embedder
per database. Still to come: cross-tier **federation** — routing reads and
fanning out writes across private, shared, and global stores, and a streaming
search that merges results from all tiers.

## Adapters

::: arcana.memory.adapters.sqlite.SQLiteAdapter

::: arcana.memory.adapters.vector.VectorAdapter

## Embedding gateway

::: arcana.memory.embedding_gateway.EmbeddingGateway

## Migrations

::: arcana.memory.migrations.migrate_to_latest

::: arcana.memory.migrations.latest_version

## Errors

::: arcana.memory.errors.MemoryError

::: arcana.memory.errors.MemoryStorageError

::: arcana.memory.errors.MemoryNotConnectedError
