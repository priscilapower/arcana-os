"""Schema migration definitions — one module per version, assembled in order.

Each ``vNNN_*.py`` module exports a single ``MIGRATION`` tuple of the shape
``(target_version, [DDL statements run in order, in one transaction])``. The
runner bumps ``user_version`` in that same transaction, so a failure rolls both
the DDL and the version bump back together.

Convention: **append-only**. Never edit a shipped migration — add a new
``vNNN_*.py`` module and register it in ``MIGRATIONS`` below, in ascending
version order:

    v1  memory_entries
    v2  memory_entries_fts (FTS5 keyword index + sync triggers)
    v3  embedding_meta (per-database embedding-model pin)
"""

from arcana.memory.migrations.versions.v001_memory_entries import MIGRATION as _v001
from arcana.memory.migrations.versions.v002_memory_entries_fts import MIGRATION as _v002
from arcana.memory.migrations.versions.v003_embedding_meta import MIGRATION as _v003

# Registry consumed by the runner. Order matters: ascending by version.
MIGRATIONS: list[tuple[int, list[str]]] = [
    _v001,
    _v002,
    _v003,
]
