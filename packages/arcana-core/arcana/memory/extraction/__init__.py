"""Memory extraction — turn a completed exchange into typed, scored memories.

Extraction is a strategy object the agent owns: it produces a small list of
:class:`~arcana.types.memory.MemoryEntry`, each classified by ``MemoryType`` (so
the right decay profile applies) and given an *honest* confidence. Agent-asserted
text is capped below ``1.0`` and sourced as ``AGENT`` so a hallucinated fact can
be overridden by better evidence later — the anti-poisoning guarantee.

The package is split by responsibility:

- :mod:`~arcana.memory.extraction.extractors` — the ``MemoryExtractor`` interface
  and its two implementations (``HeuristicExtractor``, ``LLMExtractor``) plus the
  ``build_extractor`` factory.
- :mod:`~arcana.memory.extraction.scoring` — pure, gateway-free text/scoring
  helpers and the model-free summary/consolidation.
- :mod:`~arcana.memory.extraction.signals` — language cues (type, English data,
  detectors, registry), decoupled so a new language never touches the extractor.
- :mod:`~arcana.memory.extraction.config` — env-overridable tunables and the
  ``ExtractionConfig`` sub-block.
- :mod:`~arcana.memory.extraction.prompts` — the LLM system prompts.

Extraction is best-effort throughout: callers swallow and log failures, so it can
never crash a user-facing run.
"""

from arcana.memory.extraction.config import (
    DEFAULT_AGENT_CONFIDENCE_CAP,
    DEFAULT_MIN_CONFIDENCE_TO_STORE,
    MAX_ENTRY_CONTENT,
    SUMMARY_IMPORTANCE,
    USER_CONFIRMED_CONFIDENCE,
    ExtractionConfig,
)
from arcana.memory.extraction.extractors import (
    HeuristicExtractor,
    LLMExtractor,
    MemoryExtractor,
    build_extractor,
)
from arcana.memory.extraction.scoring import (
    build_consolidated_entry,
    compute_importance,
    filter_storable,
    heuristic_summary,
    trim_content,
)
from arcana.memory.extraction.signals import has_durable_signal
from arcana.types import ExtractionStrategy

__all__ = [
    # Strategies
    "MemoryExtractor",
    "HeuristicExtractor",
    "LLMExtractor",
    "build_extractor",
    # Config
    "ExtractionConfig",
    "ExtractionStrategy",
    # Scoring / consolidation
    "build_consolidated_entry",
    "filter_storable",
    "heuristic_summary",
    "compute_importance",
    "trim_content",
    "has_durable_signal",
    # Tunable constants
    "DEFAULT_AGENT_CONFIDENCE_CAP",
    "DEFAULT_MIN_CONFIDENCE_TO_STORE",
    "USER_CONFIRMED_CONFIDENCE",
    "SUMMARY_IMPORTANCE",
    "MAX_ENTRY_CONTENT",
]
