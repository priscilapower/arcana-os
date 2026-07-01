"""Arcana memory backends - Memory Federation.

Concrete ``MemoryAdapter`` implementations and their schema migrations. The
adapter protocol itself lives in ``arcana.types.memory``; this package holds the
storage layer behind it.
"""

from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.adapters.vector import VectorAdapter
from arcana.memory.config import MemoryResilienceConfig, TierResilienceConfig
from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.memory.errors import (
    MemoryCorruptError,
    MemoryError,
    MemoryNotConnectedError,
    MemoryRoutingError,
    MemoryStorageError,
    MemoryWriteError,
    TierWriteFailed,
)
from arcana.memory.federation import MemoryFederation
from arcana.memory.resilience import BreakerState, CircuitBreaker, ResilientTier
from arcana.memory.router import GLOBAL_PROMOTION_THRESHOLD, MemoryRouter, TierBackend

__all__ = [
    "EmbeddingGateway",
    "SQLiteAdapter",
    "VectorAdapter",
    "MemoryFederation",
    "MemoryRouter",
    "TierBackend",
    "GLOBAL_PROMOTION_THRESHOLD",
    # Resilience
    "ResilientTier",
    "CircuitBreaker",
    "BreakerState",
    "MemoryResilienceConfig",
    "TierResilienceConfig",
    # Errors
    "MemoryError",
    "MemoryStorageError",
    "MemoryCorruptError",
    "MemoryNotConnectedError",
    "MemoryRoutingError",
    "MemoryWriteError",
    "TierWriteFailed",
]
