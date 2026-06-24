"""v2 — FTS5 keyword index over ``memory_entries`` + sync triggers.

Provides keyword (BM25) relevance search; the triggers keep the index in
lockstep with ``memory_entries`` on every write.
"""

MIGRATION: tuple[int, list[str]] = (
    2,
    [
        # FTS5 keyword index. Standalone (contentful) table rather than an
        # external-content one: memory_entries is keyed by TEXT uuid (no INTEGER
        # PRIMARY KEY), so an external-content table would have to ride the
        # implicit, VACUUM-unstable rowid. We instead carry the uuid in an
        # UNINDEXED ``entry_id`` column and join on it. Cost is ~1x the content
        # text on disk — negligible for a local-first single-user store.
        # ``porter unicode61`` gives English stemming + diacritic folding.
        """
        CREATE VIRTUAL TABLE memory_entries_fts USING fts5(
            entry_id UNINDEXED,
            content,
            tags,
            tokenize = 'porter unicode61 remove_diacritics 2'
        )
        """,
        # Backfill rows that v1 already wrote, so upgrading an existing
        # database makes its prior entries immediately keyword-searchable.
        """
        INSERT INTO memory_entries_fts (entry_id, content, tags)
        SELECT id, content, tags FROM memory_entries
        """,
        # Triggers keep the index in lockstep with the source of truth on every
        # write path — including the adapter's INSERT ... ON CONFLICT DO UPDATE
        # (fires AFTER UPDATE) — atomically, with no extra round-trip in write().
        # The DELETE trigger has no caller yet (writes are upserts) but costs nothing.
        """
        CREATE TRIGGER memory_entries_fts_ai AFTER INSERT ON memory_entries BEGIN
            INSERT INTO memory_entries_fts (entry_id, content, tags)
            VALUES (new.id, new.content, new.tags);
        END
        """,
        """
        CREATE TRIGGER memory_entries_fts_ad AFTER DELETE ON memory_entries BEGIN
            DELETE FROM memory_entries_fts WHERE entry_id = old.id;
        END
        """,
        """
        CREATE TRIGGER memory_entries_fts_au AFTER UPDATE ON memory_entries BEGIN
            DELETE FROM memory_entries_fts WHERE entry_id = old.id;
            INSERT INTO memory_entries_fts (entry_id, content, tags)
            VALUES (new.id, new.content, new.tags);
        END
        """,
    ],
)
