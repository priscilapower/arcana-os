"""Language signals for heuristic extraction.

The deterministic extractor decides an entry's *type* and *importance* from
surface cues — imperative/"remember" language, stated preferences, how-to
questions, step lists. Those cues are language-specific, so they live here rather
than in the extractor, split by responsibility:

- :mod:`~arcana.memory.extraction.signals.patterns` — the ``SignalPatterns`` type.
- :mod:`~arcana.memory.extraction.signals.english` — the English patterns (data).
- :mod:`~arcana.memory.extraction.signals.detectors` — the predicates that read them.
- :mod:`~arcana.memory.extraction.signals.registry` — register/look up by ISO code.

Adding a language is a new data module plus a ``register_language`` call; the
detectors and the extractor never change.
"""

from arcana.memory.extraction.signals.detectors import (
    has_durable_signal,
    has_imperative,
    has_steps,
    is_howto,
)
from arcana.memory.extraction.signals.english import ENGLISH
from arcana.memory.extraction.signals.patterns import SignalPatterns
from arcana.memory.extraction.signals.registry import (
    DEFAULT_LANGUAGE,
    get_signals,
    register_language,
)

__all__ = [
    "SignalPatterns",
    "ENGLISH",
    "DEFAULT_LANGUAGE",
    "register_language",
    "get_signals",
    "has_imperative",
    "has_durable_signal",
    "is_howto",
    "has_steps",
]
