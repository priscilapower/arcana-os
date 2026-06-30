"""Arcana memory backends - Memory Federation.

Concrete ``MemoryAdapter`` implementations and their schema migrations. The
adapter protocol itself lives in ``arcana.types.memory``; this package holds the
storage layer behind it.
"""

from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.adapters.vector import VectorAdapter
from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.memory.errors import (
    MemoryError,
    MemoryNotConnectedError,
    MemoryRoutingError,
    MemoryStorageError,
)
from arcana.memory.router import GLOBAL_PROMOTION_THRESHOLD, MemoryRouter, TierBackend

__all__ = [
    "EmbeddingGateway",
    "SQLiteAdapter",
    "VectorAdapter",
    "MemoryRouter",
    "TierBackend",
    "GLOBAL_PROMOTION_THRESHOLD",
    "MemoryError",
    "MemoryStorageError",
    "MemoryNotConnectedError",
    "MemoryRoutingError",
]
