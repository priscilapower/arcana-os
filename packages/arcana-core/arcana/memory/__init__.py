"""Arcana memory backends - Memory Federation.

Concrete ``MemoryAdapter`` implementations and their schema migrations. The
adapter protocol itself lives in ``arcana.types.memory``; this package holds the
storage layer behind it.
"""

from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.memory.errors import (
    MemoryError,
    MemoryNotConnectedError,
    MemoryStorageError,
)

__all__ = [
    "EmbeddingGateway",
    "SQLiteAdapter",
    "MemoryError",
    "MemoryStorageError",
    "MemoryNotConnectedError",
]
