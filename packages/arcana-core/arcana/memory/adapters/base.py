"""MemoryAdapter ABC — every memory backend implements this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from arcana.types.memory import AdapterCapabilities, AdapterHealth, MemoryEntry, MemoryQuery


class MemoryAdapter(ABC):
    """
    The universal memory interface. Agents call this — they never know
    which backend(s) are underneath.
    """

    adapter_id: str = "base"
    capabilities: AdapterCapabilities = AdapterCapabilities()

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
