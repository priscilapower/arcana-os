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
)
