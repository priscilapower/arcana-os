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

## Folder connector (Markdown)

`MarkdownFolderAdapter` exposes a directory of Markdown notes as retrievable
memory — the zero-setup way to make an Obsidian vault (or any notes folder)
searchable by an agent. It implements the same `MemoryAdapter` protocol, so it
registers as a federation tier or attaches to an agent's memory slot with
nothing but a folder path: no plugin, no server, no sync job.

One `.md`/`.markdown` file becomes one `MemoryEntry`. The id is a stable `uuid5`
of the file's root-relative path, so re-reads — and any future ingest into
SQLite — upsert instead of duplicating, and the federation dedups a folder note
against an ingested copy by that shared id. YAML frontmatter maps to fields
(`type`, `importance`, `pinned`, `tags`), Obsidian `#hashtags` fold into tags,
and malformed frontmatter is tolerated — one bad note never fails the scan.

The folder has no FTS5 or vector index, so every retrieval mode **collapses to
keyword**: a `semantic` or `hybrid` query is served by the same in-process
substring/token scan with a one-line degraded notice, never an error. Results
rank pinned → lexical relevance → importance → recency. A process-local index
cache keyed by path → (mtime, size) keeps a live read fresh — each `search()`
restats the tree and re-parses only changed or new files — so the connector
works standalone with zero sync infrastructure.

It is **read-only**: reads never mutate files, and `write()` raises
`MemoryWriteError` (the folder is an external source of truth, not a sink).
Dotfiles and dot-dirs (`.obsidian/`, `.trash/`), symlinks, oversized files
(`max_file_bytes`, default 1 MiB), and any configurable ignore-glob are skipped;
`health_check()` is a cheap `stat` + readability probe on the root and never
raises.

```python
from pathlib import Path
from uuid import uuid4

from arcana.memory import MarkdownFolderAdapter
from arcana.types import MemoryQuery, MemoryScope

vault = MarkdownFolderAdapter(
    Path("~/Documents/MyVault").expanduser(),
    agent_id=uuid4(),
    scope=MemoryScope.SHARED,        # e.g. registered as a shared read tier
    pool_name="vault",
)
results = await vault.search(MemoryQuery(text="metric units", limit=5))
```

## Pruning

`prune()` removes low-value entries per a
[`PrunePolicy`](types.md#arcana.types.memory.PrunePolicy). The `min_importance`
floor and the `max_entries` cap compose into one victim set (an entry is removed
if it falls below the floor *or* sits outside the top-N by importance), and
**pinned entries are never touched**. `PruneMode.ARCHIVE` soft-deletes (sets
`archived`, hiding the entry from search but keeping it recoverable);
`PruneMode.PURGE` hard-deletes the row — and its vector, if a `vec0` index
exists. A [`PruneReport`](types.md#arcana.types.memory.PruneReport) records what
was scanned, archived, and purged.

```python
from arcana.types import PrunePolicy, PruneMode

report = await memory.prune(PrunePolicy(min_importance=0.2, max_entries=10_000))
```

## Knowledge graph (edges)

Beyond similarity, memory carries an explicit graph: typed, directed **edges**
between nodes, stored in a `memory_edges` table (migration `v4`) that lives in
the same SQLite database as the rows — a property graph, no separate engine.
`EdgeStore` is its read/write layer. An edge is a
[`MemoryEdge`](types.md#arcana.types.memory.MemoryEdge): `src_id → dst_id` under
a `relation`, tagged by the `source` that produced it and carrying a
`confidence`. Endpoints are stable node ids, so an edge can relate nodes that
live in any tier — a folder connector's `uuid5` note id is a valid endpoint even
though it is not a stored row.

The `(src_id, dst_id, relation)` primary key makes writes idempotent. A producer
owns its `source` and rewrites exactly that set with `replace_source()`, so a
re-index stays convergent as links are added, removed, or re-pointed.
`neighbors()` returns the ids one hop away in either direction — the seed→expand
primitive for graph-aware retrieval.

### Wikilinks → edges

`WikilinkEdgeExtractor` populates the graph from a Markdown folder: it parses
Obsidian `[[wikilinks]]` (and `![[embeds]]`) out of the notes a
`MarkdownFolderAdapter` already reads and writes them as `references` edges. It
is fully **deterministic** — regex over text, resolution by filename or path, no
model in the loop — so it carries none of the hallucinated-edge risk that
inferred edges would. Targets resolve within the folder (a bare `[[Note]]` by
basename, `[[folder/Note]]` by path); ambiguous and dangling links are skipped
and counted, never guessed. Each `reindex()` rewrites the whole `wikilink` edge
set and returns an `EdgeIndexReport` tally.

```python
from arcana.memory import (
    EdgeStore,
    MarkdownFolderAdapter,
    SQLiteAdapter,
    WikilinkEdgeExtractor,
)

reader = MarkdownFolderAdapter(vault_path, agent_id)
edges = EdgeStore(SQLiteAdapter.for_agent(agent_id))
await edges.connect()

report = await WikilinkEdgeExtractor(reader, edges).reindex()
neighbours = await edges.neighbors(note_id)   # ids one hop away
```

!!! note "Populates, doesn't traverse"
    The extractor writes edges; consuming them at read time — seed by
    vector/keyword, expand along edges, re-rank — is a federation concern and is
    not wired into the read path yet.

## Federation across tiers

A `MemoryFederation` presents many tier backends as a single `MemoryAdapter`, so
an Agent holds it exactly where it would hold one store and the topology stays
invisible. Three scopes make up the topology:

- **private** — the agent's own store (the durability anchor);
- **shared** — named pools an agent group reads and writes;
- **global** — the shared-by-all tier, the mechanism behind "all agents read;
  The World writes".

A `MemoryRouter` owns the pure routing policy; the federation performs the I/O.

- **Fan-out writes** — `route_write()` decides the target tiers, and the
  federation writes each concurrently. A high-importance (`>= 0.9`) `PRIVATE`
  entry is also written to `GLOBAL` as a scope-rewritten copy (promotion, same
  `id`). Writes are **not transactional** across tiers.
- **Merged reads** — `route_read()` fans a query across the routed tiers
  concurrently; results are deduplicated by `id` (the most-local tier wins) and
  re-ranked by the agent's [`MemoryWeights`](cards.md) before truncation to
  `query.limit`. `stream_search()` yields the same ranked sequence one entry at
  a time.

Reads and writes treat failure differently, on purpose: a failed *read* tier is
dropped so the agent still sees the other tiers' results (partial memory beats
none), while a failed *private write* raises `MemoryWriteError` — a lost write to
the durability anchor is data loss the caller must know about. `SHARED`/`GLOBAL`
write failures degrade instead (a degraded `GLOBAL` simply pauses promotion).

```python
from arcana.memory import MemoryFederation, MemoryRouter, SQLiteAdapter

router = MemoryRouter(
    private=SQLiteAdapter.for_agent(agent_id),
    global_=SQLiteAdapter(global_db_path),
    pools={"team-research": SQLiteAdapter(pool_db_path)},
    weights=agent_config.memory_weights,
)
memory = MemoryFederation(router)

await memory.write(entry)                    # fans out to every routed tier
results = await memory.search(MemoryQuery(agent_id=agent_id))  # merged + ranked
```

## Assembling a federation for an agent

Wiring the router and tiers by hand (above) is the low-level API. In practice one
call does the whole assembly: `build_federation()` turns an agent id plus the
`~/.arcana` home into a ready `MemoryFederation`.

```python
from pathlib import Path
from uuid import uuid4

from arcana.memory import EmbeddingGateway, PoolConfig, build_federation
from arcana.models.adapters.fastembed_embedding import FastEmbedEmbeddingAdapter

federation = await build_federation(
    uuid4(),
    home=Path.home() / ".arcana",
    embedding=EmbeddingGateway([FastEmbedEmbeddingAdapter()]),  # optional
    pools=[PoolConfig("team-research", pool_adapter)],          # optional
)
# ... agent.run(...) ...
await federation.aclose()   # release the private handle and any vector store
```

It builds the tiers the way the runtime expects:

- **private** — per-agent SQLite at `~/.arcana/agents/{id}/memory.db`, opened
  eagerly so [migrations](#schema-migrations) run and a corrupt store is
  quarantined before first use. This tier is always present, the durability
  anchor.
- **global** — a shared vector store at `~/.arcana/vector/global.db`, wired only
  when an `EmbeddingGateway` is supplied. It is semantic when the embedder is
  healthy and keyword (FTS5) when not; with no embedder the tier is dropped and
  the agent runs private-only, so a zero-config install still works.
- **shared** — each `PoolConfig` is registered on the router by name.

Degradations route to the [observability audit log](observability.md#events) by
default (pass `on_degraded` to override), so a thinned `SHARED`/`GLOBAL` tier is
visible without failing the run.

### Injection and configuration

Callers rarely invoke `build_federation` directly.
[`AgentRegistry.build_runtime_with_memory()`](agent.md) assembles and injects a
federation for a stored agent, returning `(agent, federation)` so the caller owns
teardown. The CLI `arcana run` path uses it, so **agents remember across sessions
by default**. Opt out of a single run with `--no-memory`, or globally via the
`memory` block in `~/.arcana/config.json`:

```json
{
  "memory": { "enabled": true, "private": "sqlite", "global": "vector", "pools": [] }
}
```

The global vector tier activates when an embedding provider is available: the CLI
uses in-process [fastembed](https://github.com/qdrant/fastembed) when the
`arcana-os[embed]` extra is installed, and stays private-SQLite-only otherwise.

## Resilience

The router wraps every tier in a `ResilientTier` before handing it to the
federation, so a slow, locked, or corrupt store can never stall or sink a whole
session. Each wrapper bounds calls with a **timeout**, contains failures behind a
per-tier **circuit breaker**, and surfaces the thinning as a
[`MemoryDegradedEvent`](observability.md#events) rather than swallowing it
silently.

- **Reads are total** — a timeout, open breaker, corruption, or backend error
  yields `[]` after emitting a degraded event.
- **Writes are partial** — the same conditions raise `TierWriteFailed` carrying
  the tier's scope, so the federation decides the blast radius (`PRIVATE` fatal,
  `SHARED`/`GLOBAL` degrade).

A `CircuitBreaker` trips after `fail_threshold` consecutive failures during real
traffic, fails fast while `OPEN`, then allows one `HALF_OPEN` probe once
`reset_after_seconds` elapses. Corruption is special: a `MemoryCorruptError` is a
session-long condition, so it *forces* the breaker open (quarantine) rather than
counting as one transient failure.

Timeout budgets and breaker thresholds are per tier — a keyword read hits local
SQLite, while a semantic read may call a remote embedder — and configurable via
`~/.arcana/connections/memory-adapters.json` (a missing file yields safe
defaults, so existing programmatic wiring keeps working):

```json
{
  "private":       { "read_timeout_ms": 250, "semantic_timeout_ms": 1500, "write_timeout_ms": 500 },
  "global":        { "read_timeout_ms": 400, "write_timeout_ms": 600 },
  "shared":        { "team-research": { "read_timeout_ms": 400 } },
  "default_shared": { "read_timeout_ms": 400, "write_timeout_ms": 600 }
}
```

## Background jobs

Post-session extraction and periodic consolidation are meant to run *off* the
request path. `BackgroundJobQueue` models that as a bounded queue with
load-shedding: `submit()` never blocks, always accepting **critical** jobs (e.g.
a user-confirmed preference) while shedding **non-critical** jobs once the
backlog's drain estimate (`depth × EWMA(service time)`) exceeds its headroom.

!!! note "Design-ahead, not yet wired"
    Extraction is still an inline synchronous write in `Agent`; nothing drains
    this queue in the live path yet. Only the queue interface and its
    depth/drain metrics ship today — the consumer loop lands when consolidation
    actually moves off the request path.

## Adapters

::: arcana.memory.adapters.sqlite.SQLiteAdapter

::: arcana.memory.adapters.vector.VectorAdapter

::: arcana.memory.adapters.markdown.MarkdownFolderAdapter

::: arcana.memory.adapters.markdown.ScannedNote

## Knowledge graph

::: arcana.memory.edges.EdgeStore

::: arcana.memory.wikilinks.WikilinkEdgeExtractor

::: arcana.memory.wikilinks.EdgeIndexReport

## Federation and routing

::: arcana.memory.assembly.build_federation

::: arcana.memory.assembly.PoolConfig

::: arcana.memory.assembly.MemoryConfig

::: arcana.memory.federation.MemoryFederation

::: arcana.memory.router.MemoryRouter

::: arcana.memory.router.TierBackend

## Resilience

::: arcana.memory.resilience.ResilientTier

::: arcana.memory.resilience.CircuitBreaker

::: arcana.memory.resilience.BreakerState

## Configuration

::: arcana.memory.config.MemoryResilienceConfig

::: arcana.memory.config.TierResilienceConfig

## Background jobs

::: arcana.memory.jobs.BackgroundJobQueue

::: arcana.memory.jobs.MemoryJob

::: arcana.memory.jobs.MemoryJobKind

## Embedding gateway

::: arcana.memory.embedding_gateway.EmbeddingGateway

## Migrations

::: arcana.memory.migrations.migrate_to_latest

::: arcana.memory.migrations.latest_version

## Errors

::: arcana.memory.errors.MemoryError

::: arcana.memory.errors.MemoryStorageError

::: arcana.memory.errors.MemoryCorruptError

::: arcana.memory.errors.MemoryNotConnectedError

::: arcana.memory.errors.MemoryRoutingError

::: arcana.memory.errors.MemoryWriteError

::: arcana.memory.errors.TierWriteFailed
