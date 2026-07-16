"""Arcana memory backends - Memory Federation.

Concrete ``MemoryAdapter`` implementations and their schema migrations. The
adapter protocol itself lives in ``arcana.types.memory``; this package holds the
storage layer behind it.
"""

from arcana.memory.adapters.markdown import MarkdownFolderAdapter, ScannedNote
from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.adapters.vector import VectorAdapter
from arcana.memory.assembly import MemoryConfig, PoolConfig, build_federation, load_memory_config
from arcana.memory.config import MemoryResilienceConfig, TierResilienceConfig
from arcana.memory.decay import (
    decay_sorted,
    effective_importance,
    resolve_decay_profiles,
    should_consolidate,
)
from arcana.memory.edges import EdgeStore
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
from arcana.memory.extraction import (
    ExtractionConfig,
    HeuristicExtractor,
    LLMExtractor,
    MemoryExtractor,
    build_consolidated_entry,
    build_extractor,
    compute_importance,
    filter_storable,
    heuristic_summary,
    trim_content,
)
from arcana.memory.extraction.signals import (
    ENGLISH,
    SignalPatterns,
    get_signals,
    register_language,
)
from arcana.memory.federation import MemoryFederation
from arcana.memory.jobs import BackgroundJobQueue, MemoryJob, MemoryJobKind
from arcana.memory.resilience import BreakerState, CircuitBreaker, ResilientTier
from arcana.memory.router import GLOBAL_PROMOTION_THRESHOLD, MemoryRouter, TierBackend
from arcana.memory.wikilinks import (
    WIKILINK_RELATION,
    WIKILINK_SOURCE,
    EdgeIndexReport,
    WikilinkEdgeExtractor,
)

__all__ = [
    "EmbeddingGateway",
    "MarkdownFolderAdapter",
    "ScannedNote",
    "SQLiteAdapter",
    "VectorAdapter",
    # Knowledge graph
    "EdgeStore",
    "WikilinkEdgeExtractor",
    "EdgeIndexReport",
    "WIKILINK_RELATION",
    "WIKILINK_SOURCE",
    "MemoryFederation",
    "MemoryRouter",
    "TierBackend",
    "GLOBAL_PROMOTION_THRESHOLD",
    # Decay
    "effective_importance",
    "should_consolidate",
    "decay_sorted",
    "resolve_decay_profiles",
    # Assembly
    "build_federation",
    "PoolConfig",
    "MemoryConfig",
    "load_memory_config",
    # Extraction
    "MemoryExtractor",
    "HeuristicExtractor",
    "LLMExtractor",
    "ExtractionConfig",
    "build_extractor",
    "build_consolidated_entry",
    "filter_storable",
    "heuristic_summary",
    "compute_importance",
    "trim_content",
    # Extraction signals (i18n)
    "SignalPatterns",
    "ENGLISH",
    "get_signals",
    "register_language",
    # Resilience
    "ResilientTier",
    "CircuitBreaker",
    "BreakerState",
    "MemoryResilienceConfig",
    "TierResilienceConfig",
    # Backpressure
    "BackgroundJobQueue",
    "MemoryJob",
    "MemoryJobKind",
    # Errors
    "MemoryError",
    "MemoryStorageError",
    "MemoryCorruptError",
    "MemoryNotConnectedError",
    "MemoryRoutingError",
    "MemoryWriteError",
    "TierWriteFailed",
]
