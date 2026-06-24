"""v1 — the ``memory_entries`` table and its query indexes."""

MIGRATION: tuple[int, list[str]] = (
    1,
    [
        """
        CREATE TABLE memory_entries (
            id                TEXT PRIMARY KEY,
            agent_id          TEXT NOT NULL,
            type              TEXT NOT NULL,
            content           TEXT NOT NULL,
            scope             TEXT NOT NULL DEFAULT 'private',
            pool_name         TEXT,
            importance        REAL NOT NULL DEFAULT 0.5,
            pinned            INTEGER NOT NULL DEFAULT 0,
            is_consolidated   INTEGER NOT NULL DEFAULT 0,
            consolidated_from TEXT NOT NULL DEFAULT '[]',
            confidence        REAL NOT NULL DEFAULT 1.0,
            confidence_source TEXT NOT NULL DEFAULT 'agent',
            has_conflict      INTEGER NOT NULL DEFAULT 0,
            conflict_id       TEXT,
            embedding         TEXT,
            source_session_id TEXT,
            tags              TEXT NOT NULL DEFAULT '[]',
            created_at        TEXT NOT NULL,
            last_accessed_at  TEXT NOT NULL,
            access_count      INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX idx_mem_scope_agent ON memory_entries(scope, agent_id)",
        "CREATE INDEX idx_mem_type        ON memory_entries(type)",
        "CREATE INDEX idx_mem_pool        ON memory_entries(pool_name)",
        "CREATE INDEX idx_mem_importance  ON memory_entries(importance DESC)",
        "CREATE INDEX idx_mem_created     ON memory_entries(created_at DESC)",
    ],
)
