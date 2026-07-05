"""Language registry — register and look up :class:`SignalPatterns` by ISO code.

The extractor resolves a language through here, so a new language becomes
available process-wide the moment it is registered, without touching call sites.
"""

from arcana.memory.extraction.signals.english import ENGLISH
from arcana.memory.extraction.signals.patterns import SignalPatterns

DEFAULT_LANGUAGE = "en"

_REGISTRY: dict[str, SignalPatterns] = {DEFAULT_LANGUAGE: ENGLISH}


def register_language(code: str, patterns: SignalPatterns) -> None:
    """Register a language's signal patterns under an ISO code (e.g. ``"pt"``)."""
    _REGISTRY[code] = patterns


def get_signals(language: str = DEFAULT_LANGUAGE) -> SignalPatterns:
    """Return the signals for *language*, falling back to English if unknown."""
    return _REGISTRY.get(language, ENGLISH)
