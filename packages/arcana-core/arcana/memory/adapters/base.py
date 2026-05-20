"""MemoryAdapter ABC — every memory backend implements this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from arcana.types.memory import AdapterCapabilities, AdapterHealth, MemoryEntry, MemoryQuery


class WriteDurability(str, Enum):
    """
    Whether a write is immediately consistent or eventually consistent.

    ACKNOWLEDGED — the entry is durable and immediately readable (SQLite, Postgres).
    EVENTUAL     — the write is accepted but may not be visible to reads for a short
                   period (Elasticsearch index refresh, some cloud stores).

    Callers that need guaranteed read-after-write should check durability and,
    if EVENTUAL, either wait or operate on the in-memory entry directly.
    """
    ACKNOWLEDGED = "acknowledged"
    EVENTUAL = "eventual"


class MemoryAdapter(ABC):
    """
    The universal memory interface. Agents call this — they never know
    which backend(s) are underneath.
    """

    adapter_id: str = "base"
    capabilities: AdapterCapabilities = AdapterCapabilities()

    # Declare the write consistency model for this backend.
    # Concrete adapters override this class attribute.
    # Phase 1/2 adapters (SQLite, Postgres) always use ACKNOWLEDGED.
    write_durability: WriteDurability = WriteDurability.ACKNOWLEDGED

    @abstractmethod
    async def connect(self) -> None:
        """Initialise the backend (open DB connection, auth, etc.)."""
        ...

    @abstractmethod
    async def write(self, entry: MemoryEntry) -> str:
        """Persist a memory entry. Returns the entry id."""
        ...

    @abstractmethod
    async def read(self, entry_id: str) -> MemoryEntry | None:
        """Fetch a single entry by id."""
        ...

    @abstractmethod
    async def forget(self, entry_id: str) -> None:
        """Delete a memory entry permanently."""
        ...

    @abstractmethod
    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Search for entries matching a query."""
        ...

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        """Return the adapter's health status."""
        ...

    async def close(self) -> None:
        """Optional: clean up connections."""
        pass
