"""Forward-only schema migration runner driven by SQLite's ``PRAGMA user_version``.

Deliberately dependency-free: no Alembic, no SQLAlchemy. ``arcana-core`` is a
lean, local-first library and the adapters talk raw ``aiosqlite``, so a full
migration framework is unjustified. ``PRAGMA user_version`` is a 32-bit integer
stored in the database header — exactly the primitive a forward-only runner needs.

This module is the *engine* only. The migrations themselves live in ``versions/``
— one module per schema version — so adding a migration never touches this file.
"""

import aiosqlite

from arcana.memory.migrations.versions import MIGRATIONS


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
