"""Detection helpers — pure predicates over a :class:`SignalPatterns`.

Each takes a ``SignalPatterns`` (English by default) so a caller can detect in
another language without changing any call site. These read signals; they never
define them — the patterns live in the language modules (e.g. ``english``).
"""

from arcana.memory.extraction.signals.english import ENGLISH
from arcana.memory.extraction.signals.patterns import SignalPatterns


def has_imperative(text: str, signals: SignalPatterns = ENGLISH) -> bool:
    """True when *text* uses imperative/"remember" emphasis."""
    return bool(signals.imperative.search(text))


def has_durable_signal(text: str, signals: SignalPatterns = ENGLISH) -> bool:
    """True when *text* states a durable user fact/preference (→ SEMANTIC)."""
    return bool(signals.preference.search(text))


def is_howto(text: str, signals: SignalPatterns = ENGLISH) -> bool:
    """True when *text* asks for a procedure/how-to."""
    return bool(signals.howto.search(text))


def has_steps(text: str, signals: SignalPatterns = ENGLISH) -> bool:
    """True when *text* contains a step/ordered list."""
    return bool(signals.steps.search(text))
