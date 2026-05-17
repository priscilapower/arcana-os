"""
KnowledgeConnector ABC — external knowledge sources.

Distinct from MemoryAdapter:
  - MemoryAdapter: Arcana OWNS the store (SQLite, vector db)
  - KnowledgeConnector: User OWNS the source (Obsidian, Notion, files)

Connectors are read-first. Write-back is optional and explicit.
Arcana indexes embeddings for fast retrieval but never duplicates
full content — source of truth always stays in the external system.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class SyncStrategy(str, Enum):
    LIVE = "live"           # query source directly every time (always fresh)
    PERIODIC = "periodic"   # background index, configurable interval
    MANUAL = "manual"       # user triggers re-index explicitly


class KnowledgeChunk(BaseModel):
    """
    A piece of content from an external source.
    NOT a MemoryEntry — Arcana does not own this.
    """

    id: str                              # source-specific identifier
    content: str                         # the actual text
    source_uri: str                      # e.g. "obsidian://Daily Notes/2024-03-15"
    title: str | None = None
    tags: list[str] = []
    last_modified: datetime | None = None
    connector_id: str = ""

    # Arcana caches the embedding for fast retrieval
    # but fetches live content from source at read time
    arcana_embedding: list[float] | None = None

    @property
    def display_source(self) -> str:
        """Human-readable source reference."""
        return self.title or self.source_uri


class ConnectorHealth(BaseModel):
    connector_id: str
    healthy: bool
    message: str = ""
    last_synced: datetime | None = None


class KnowledgeConnector(ABC):
    """
    Base class for all external knowledge sources.

    Implement search() and fetch() at minimum.
    Override write_back() only if the source supports it.
    """

    connector_id: str = "base"
    readonly: bool = True
    requires_auth: bool = False
    sync_strategy: SyncStrategy = SyncStrategy.LIVE

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the external source."""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[KnowledgeChunk]:
        """
        Search the external source for relevant chunks.
        Uses embeddings if indexed, otherwise queries live.
        """
        ...

    @abstractmethod
    async def fetch(self, chunk_id: str) -> KnowledgeChunk | None:
        """Fetch the full live content of a specific chunk."""
        ...

    async def write_back(self, chunk: KnowledgeChunk) -> str | None:
        """
        Optionally write content back to the external source.
        Returns the new chunk id if successful, None if not supported.
        Default: not supported (readonly).
        """
        return None

    async def sync(self) -> int:
        """
        Index/re-index the source into Arcana's embedding cache.
        Returns number of chunks indexed.
        Default: no-op for LIVE strategy.
        """
        return 0

    @abstractmethod
    async def health_check(self) -> ConnectorHealth:
        ...

    async def close(self) -> None:
        pass
