"""Memory backend error taxonomy.

Concrete adapters translate backend-specific exceptions (e.g. ``sqlite3.Error``)
into these at the boundary, so callers never see raw driver exceptions — mirrors
the ``ModelAdapter._translate`` contract in ``arcana.models``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arcana.observability.events import MemoryDegradeReason
    from arcana.types import MemoryScope


class MemoryError(Exception):
    """Base class for all memory-backend errors."""


class MemoryStorageError(MemoryError):
    """A read/write against the backend failed (I/O, corruption, constraint)."""


class MemoryCorruptError(MemoryStorageError):
    """The backend reported a malformed or unreadable database image.

    A subclass of ``MemoryStorageError`` so existing ``except MemoryStorageError``
    sites still catch it, but distinct so the resilience layer can treat it
    specially: corruption is a session-long condition, not a transient failure,
    so a tier that raises this is quarantined (breaker forced open) rather than
    merely counted as one failure.
    """


class MemoryNotConnectedError(MemoryError):
    """The adapter was used before a connection/schema was established."""


class MemoryRoutingError(MemoryError):
    """A write targets a tier that is missing or under-specified.

    Raised when an entry must reach a backend that was never registered — a
    GLOBAL write with no global backend, or a SHARED write whose ``pool_name``
    is absent or names an unknown pool.
    """


class MemoryWriteError(MemoryError):
    """A write to an agent's PRIVATE store failed and could not be recovered.

    PRIVATE memory is the durability anchor: an agent that cannot persist its
    own memory must learn of it rather than silently lose data it believes was
    written. The federation raises this when the private write leg fails; SHARED
    and GLOBAL write failures degrade instead (surfaced via ``MemoryDegradedEvent``).
    """


class TierWriteFailed(MemoryError):
    """A single tier's write failed inside the resilience wrapper.

    Carries the ``scope`` of the failing tier so the federation can decide the
    blast radius — PRIVATE is fatal (re-raised as ``MemoryWriteError``), SHARED
    and GLOBAL degrade. ``cause`` is the original backend exception (or a
    breaker-open sentinel); ``reason`` is the degradation category
    (``timeout`` / ``breaker_open`` / ``backend_error`` / ``corruption``) the
    federation puts on the ``MemoryDegradedEvent`` it emits for a degraded tier.
    The wrapper raises rather than emits, so the federation can label the event
    ``write`` vs ``promote`` and suppress it entirely for the fatal PRIVATE case.
    """

    def __init__(
        self, scope: "MemoryScope", cause: BaseException, reason: "MemoryDegradeReason" = "backend_error"
    ) -> None:
        self.scope = scope
        self.cause = cause
        self.reason: MemoryDegradeReason = reason
        super().__init__(f"write to {scope} tier failed ({reason}): {cause!r}")
