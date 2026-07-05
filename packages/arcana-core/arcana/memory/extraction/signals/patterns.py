"""The :class:`SignalPatterns` contract — compiled surface cues for one language.

Just the type: the concrete language definitions live in sibling modules (e.g.
``english``) and the predicates that read them in ``detectors``.
"""

from dataclasses import dataclass
from re import Pattern


@dataclass(frozen=True)
class SignalPatterns:
    """Compiled surface-cue patterns for a single language.

    - ``imperative`` — emphasis/"remember" language that raises importance.
    - ``preference`` — a durable, user-stated fact/preference (→ SEMANTIC).
    - ``howto`` — the user is asking for a procedure (paired with ``steps``).
    - ``steps`` — a step/ordered list in a response (→ PROCEDURAL).
    """

    imperative: Pattern[str]
    preference: Pattern[str]
    howto: Pattern[str]
    steps: Pattern[str]
