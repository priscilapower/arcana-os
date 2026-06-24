"""Memory schema migrations — public surface.

Splits the *runner* (``runner.py``, the engine that applies migrations) from the
*definitions* (``versions/``, one module per schema version). Re-exports both so
callers keep the flat import path:

    from arcana.memory.migrations import migrate_to_latest, latest_version, MIGRATIONS
"""

from arcana.memory.migrations.runner import latest_version, migrate_to_latest
from arcana.memory.migrations.versions import MIGRATIONS

__all__ = ["MIGRATIONS", "latest_version", "migrate_to_latest"]
