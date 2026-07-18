"""English signal patterns — the default language.

This module is pure data: it builds one :class:`SignalPatterns` and nothing else.
Adding a language means writing a sibling module in the same shape and
registering it via :func:`arcana.memory.extraction.signals.register_language`;
the detectors and the extractor never change.
"""

import re

from arcana.memory.extraction.signals.patterns import SignalPatterns

ENGLISH = SignalPatterns(
    imperative=re.compile(
        r"\b(remember|note that|don'?t forget|do not forget|keep in mind|important|"
        r"always|never|make sure|be sure to|for the record)\b",
        re.IGNORECASE,
    ),
    preference=re.compile(
        r"\b(i (prefer|like|love|hate|dislike|want|need|use|avoid|"
        r"always|never|usually|typically)\b|"
        r"my (name|email|timezone|preference|style|goal|role)\b|"
        # Self-identification only when followed by a determiner/role cue, so
        # durable facts ("I'm a developer", "I'm based in Berlin") match but
        # conversational filler ("I'm confused", "I am wondering") does not.
        r"i'?m (a|an|the|from|based|located|living|working|called|named|using)\b|"
        r"i am (a|an|the|from|based|located|living|working|called|named|using)\b|"
        r"call me\b|remember that\b)",
        re.IGNORECASE,
    ),
    howto=re.compile(
        r"\b(how (to|do|can|should|would)|steps?|procedure|process|"
        r"walk me through|guide me)\b",
        re.IGNORECASE,
    ),
    steps=re.compile(
        r"(?:^|\n)\s*(?:\d+[.)]|-\s|\*\s|step\s+\d+|first[,:]|then[,:]|finally[,:])",
        re.IGNORECASE,
    ),
    # A leading conversational/imperative wrapper around a stated fact, stripped
    # when distilling the durable clause ("remember that my project is Arcana" →
    # "my project is Arcana"). Anchored at the clause start: an optional discourse
    # marker ("also,", "oh,"), an optional "please", then a framing phrase, then a
    # trailing "that" and separators. Only these lead-ins are stripped — emphasis
    # words like "always"/"important" stay, since they belong to the fact itself.
    framing=re.compile(
        r"^[\s,.:;-]*"
        r"(?:(?:also|and|so|well|oh|hey|okay|ok|btw)[\s,:]+)?"
        r"(?:please[\s,:]+)?"
        r"(?:"
        r"i\s+(?:just\s+)?want(?:ed)?\s+you\s+to\s+know"
        r"|(?:just\s+)?so\s+you\s+know"
        r"|for\s+the\s+record"
        r"|by\s+the\s+way"
        r"|fyi"
        r"|remember"
        r"|note"
        r"|keep\s+in\s+mind"
        r"|don'?t\s+forget"
        r"|do\s+not\s+forget"
        r")"
        r"(?:[\s,:]+that)?"
        r"[\s,.:;-]+",
        re.IGNORECASE,
    ),
)
