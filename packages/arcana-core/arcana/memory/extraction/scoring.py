"""Pure scoring and text helpers for extraction.

Deterministic, side-effect-free functions the extractors compose: whitespace/
length trimming, signal-driven importance, the confidence gate, and the
model-free session summary/consolidation. Kept apart from the strategy classes
so they stay independently testable and free of any gateway dependency.
"""

import re

from arcana.memory.extraction.config import (
    DEFAULT_AGENT_CONFIDENCE_CAP,
    MAX_ENTRY_CONTENT,
    SIGNAL_BONUS,
    SUMMARY_IMPORTANCE,
)
from arcana.memory.extraction.signals import (
    ENGLISH,
    SignalPatterns,
    has_durable_signal,
    has_imperative,
)
from arcana.types import (
    ConfidenceSource,
    MemoryEntry,
    MemoryType,
    Message,
    MessageRole,
    Session,
)

# Per-field sub-limits scale with the (env-tunable) entry cap, so one knob keeps
# both a stored entry and its constituent parts bounded.
_SUMMARY_USER_CHARS = MAX_ENTRY_CONTENT // 3
_SUMMARY_ASSISTANT_CHARS = MAX_ENTRY_CONTENT // 2


def trim_content(text: str, limit: int = MAX_ENTRY_CONTENT) -> str:
    """Collapse whitespace and cap length, appending an ellipsis when truncated.

    The default ``limit`` is the env-tunable per-entry content cap; pass an
    explicit limit to frame model input. The result is never longer than
    ``limit`` characters.
    """
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


#: Sentence boundaries for isolating the clause that carries the durable cue.
#: Terminal ``.!?`` only when followed by whitespace or end-of-text (plus any
#: newline), so a mid-token dot — ``a@b.com``, ``v1.2``, a URL — is never split.
_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s+|$)|\n+")


def distill_semantic_clause(text: str, signals: SignalPatterns = ENGLISH) -> str:
    """Reduce a durable user statement to the clause worth storing as SEMANTIC.

    A stated fact usually arrives wrapped in conversational framing — *"by the
    way, please remember that my project is called Arcana"*. Persisting the whole
    prompt carries that framing into memory and back into every future prompt
    injection; this keeps the fact itself: *"my project is called Arcana"*.

    It picks the sentence bearing the durable ``preference`` cue (the same signal
    that classified the turn as SEMANTIC) and strips a leading ``framing`` wrapper,
    then trims. Falls back to the full trimmed text whenever stripping would leave
    nothing — so a bare *"remember that."* is never reduced to an empty memory.

    Pure and deterministic; ``signals`` selects the language, so a non-English cue
    set distils in its own language (or, absent framing cues, simply trims).
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    clause = next((s for s in sentences if signals.preference.search(s)), text.strip())
    distilled = signals.framing.sub("", clause, count=1).strip(" ,.:;")
    return trim_content(distilled or clause)


def compute_importance(
    text: str,
    *,
    base: float,
    pinned: bool = False,
    signals: SignalPatterns = ENGLISH,
) -> float:
    """Importance from signal, not a constant.

    Baseline per memory type, plus a bump for imperative/"remember" language and
    for a pin. Capped at ``1.0``. Deterministic for a given input.
    """
    importance = base
    if has_imperative(text, signals):
        importance += SIGNAL_BONUS
    if pinned:
        importance += SIGNAL_BONUS
    return round(min(importance, 1.0), 4)


def filter_storable(entries: list[MemoryEntry], min_confidence: float) -> list[MemoryEntry]:
    """Drop entries below ``min_confidence`` before they are written."""
    return [e for e in entries if e.confidence >= min_confidence]


def heuristic_summary(messages: list[Message]) -> str:
    """A deterministic, model-free session summary: opening ask + closing reply."""
    users = [m for m in messages if m.role == MessageRole.USER]
    assistants = [m for m in messages if m.role == MessageRole.ASSISTANT]
    parts: list[str] = []
    if users:
        parts.append(f"User asked: {trim_content(users[0].content, _SUMMARY_USER_CHARS)}")
    if assistants:
        parts.append(f"Assistant answered: {trim_content(assistants[-1].content, _SUMMARY_ASSISTANT_CHARS)}")
    return " ".join(parts)


def build_consolidated_entry(session: Session, *, signals: SignalPatterns = ENGLISH) -> MemoryEntry | None:
    """One consolidated memory carrying a session's distilled summary.

    Typed by content: a session that stated a durable user fact/preference
    consolidates as ``SEMANTIC`` (slow decay), otherwise ``EPISODIC``. Confidence
    is agent-capped — a distilled summary is still agent-asserted. Returns
    ``None`` when there is no summary to carry.

    ``signals`` selects the language cue set used to spot a durable statement
    (English by default); pass the extractor's own signals to stay consistent
    with how the turn was extracted.
    """
    if not session.summary:
        return None
    durable = any(m.role == MessageRole.USER and has_durable_signal(m.content, signals) for m in session.messages)
    mem_type = MemoryType.SEMANTIC if durable else MemoryType.EPISODIC
    return MemoryEntry(
        agent_id=session.agent_id,
        type=mem_type,
        content=session.summary,
        importance=SUMMARY_IMPORTANCE,
        confidence=DEFAULT_AGENT_CONFIDENCE_CAP,
        confidence_source=ConfidenceSource.AGENT,
        source_session_id=session.id,
    )
