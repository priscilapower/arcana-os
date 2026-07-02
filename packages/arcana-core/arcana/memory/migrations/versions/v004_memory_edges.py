"""v4 — ``memory_edges``: the typed-edge layer of the memory knowledge graph.

A directed edge relates two memory nodes by their stable ids: ``src_id`` →
``dst_id`` under a ``relation`` (e.g. ``references``), attributed to a ``source``
(e.g. ``wikilink``) and carrying a ``confidence``. The property graph lives in the
same SQLite database as ``memory_entries`` — no separate graph engine — so a read
can seed by vector/keyword and expand along edges. Endpoints are stable ids, so a
node may be resolved from any tier (a folder connector's ``uuid5`` note id need not
be a ``memory_entries`` row).

The ``(src_id, dst_id, relation)`` primary key makes edge writes idempotent; the
per-endpoint indexes serve outgoing traversal and backlinks, and the ``source``
index lets a producer replace exactly its own edges on re-index.
"""

MIGRATION: tuple[int, list[str]] = (
    4,
    [
        """
        CREATE TABLE memory_edges (
            src_id     TEXT NOT NULL,
            dst_id     TEXT NOT NULL,
            relation   TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            source     TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (src_id, dst_id, relation)
        )
        """,
        "CREATE INDEX idx_edge_src    ON memory_edges(src_id)",
        "CREATE INDEX idx_edge_dst    ON memory_edges(dst_id)",
        "CREATE INDEX idx_edge_source ON memory_edges(source)",
    ],
)
