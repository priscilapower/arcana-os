"""Forward-only schema migrations driven by SQLite's built-in ``PRAGMA user_version``.

Deliberately dependency-free: no Alembic, no SQLAlchemy. ``arcana-core`` is a
lean, local-first library and the adapters talk raw ``aiosqlite``, so a full
migration framework is unjustified. ``PRAGMA user_version`` is a 32-bit integer
stored in the database header — exactly the primitive a forward-only runner needs.

Convention (project-wide, see CLAUDE.md / memory): ``MIGRATIONS`` is **append-only**.
Never edit a shipped migration — add a new ``(version, [statements])`` entry. Each
build item that introduces schema appends here:

    v1  memory_entries
    v2  embedding_meta
    ...
"""

import aiosqlite

# Each migration is (target_version, [DDL statements run in order, in one transaction]).
# APPEND ONLY. Bumping user_version and the DDL share a transaction, so a failure
# rolls both back and the database stays at its previous version.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (
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
    ),
]


def latest_version(migrations: list[tuple[int, list[str]]] = MIGRATIONS) -> int:
    """Highest version defined. Zero when no migrations exist."""
    return migrations[-1][0] if migrations else 0


async def migrate_to_latest(
    conn: aiosqlite.Connection,
    migrations: list[tuple[int, list[str]]] = MIGRATIONS,
) -> int:
    """Apply every migration newer than the DB's current ``user_version``, in order.

    Each migration runs in its own transaction together with the ``user_version``
    bump, so a partial failure leaves the database exactly at its prior version.
    Idempotent: a database already at head applies nothing. Returns the version
    the database is at after running.

    ``migrations`` is injectable purely so tests can exercise the runner (e.g.
    rollback on a deliberately failing statement) without touching real schema.
    """
    row = await (await conn.execute("PRAGMA user_version")).fetchone()
    current: int = row[0] if row else 0

    for version, statements in migrations:
        if version <= current:
            continue
        await conn.execute("BEGIN")
        try:
            for stmt in statements:
                await conn.execute(stmt)
            # PRAGMA does not accept bound parameters; ``version`` is an int we
            # control (never user input), so interpolation is safe here.
            await conn.execute(f"PRAGMA user_version = {version}")
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        current = version

    return current
