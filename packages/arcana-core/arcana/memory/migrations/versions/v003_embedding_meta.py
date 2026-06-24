"""v3 — ``embedding_meta``: the embedding model a database is pinned to.

One row per database, written when the first embedding is stored. Locks the
database to a model so a later, incompatible embedder cannot silently corrupt
similarity scores against existing vectors. ``first_used_at`` is ISO-8601
``TEXT``, matching ``memory_entries``.
"""

MIGRATION: tuple[int, list[str]] = (
    3,
    [
        """
        CREATE TABLE embedding_meta (
            model_name    TEXT NOT NULL,
            dimensions    INTEGER NOT NULL,
            first_used_at TEXT NOT NULL,
            entry_count   INTEGER NOT NULL DEFAULT 0
        )
        """,
    ],
)
