"""Memory backend error taxonomy.

Concrete adapters translate backend-specific exceptions (e.g. ``sqlite3.Error``)
into these at the boundary, so callers never see raw driver exceptions — mirrors
the ``ModelAdapter._translate`` contract in ``arcana.models``.
"""


class MemoryError(Exception):
    """Base class for all memory-backend errors."""


class MemoryStorageError(MemoryError):
    """A read/write against the backend failed (I/O, corruption, constraint)."""


class MemoryNotConnectedError(MemoryError):
    """The adapter was used before a connection/schema was established."""
