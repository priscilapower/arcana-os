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


class MemoryRoutingError(MemoryError):
    """A write targets a tier that is missing or under-specified.

    Raised when an entry must reach a backend that was never registered — a
    GLOBAL write with no global backend, or a SHARED write whose ``pool_name``
    is absent or names an unknown pool.
    """
