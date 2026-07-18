"""The :class:`SignalPatterns` contract — compiled surface cues for one language.

Just the type: the concrete language definitions live in sibling modules (e.g.
``english``) and the predicates that read them in ``detectors``.
"""

import re
from dataclasses import dataclass
from re import Pattern

#: Default ``framing`` for a language that defines no lead-in cues: a pattern that
#: matches nothing, so clause distillation simply trims without stripping.
_NO_FRAMING: Pattern[str] = re.compile(r"(?!)")


@dataclass(frozen=True)
class SignalPatterns:
    """Compiled surface-cue patterns for a single language.

    - ``imperative`` — emphasis/"remember" language that raises importance.
    - ``preference`` — a durable, user-stated fact/preference (→ SEMANTIC).
    - ``howto`` — the user is asking for a procedure (paired with ``steps``).
    - ``steps`` — a step/ordered list in a response (→ PROCEDURAL).
    - ``framing`` — a leading conversational/imperative wrapper ("remember that",
      "by the way") stripped when distilling a durable statement down to the
      clause worth storing. Anchored at the start; defaults to a never-match
      pattern for languages that define no framing cues.
    """

    imperative: Pattern[str]
    preference: Pattern[str]
    howto: Pattern[str]
    steps: Pattern[str]
    framing: Pattern[str] = _NO_FRAMING
