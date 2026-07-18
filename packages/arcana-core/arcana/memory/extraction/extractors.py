"""Extraction strategies — the ``MemoryExtractor`` interface and its two impls.

A strategy turns a completed exchange into a small list of :class:`MemoryEntry`,
each classified by :class:`MemoryType` (so the right decay profile applies) and
given an *honest* confidence. Agent-asserted text is capped below ``1.0`` and
sourced as ``AGENT`` so a hallucinated fact can be overridden by better evidence
later — this is the anti-poisoning guarantee the ``confidence`` field exists for.

Two strategies sit behind one interface:

* :class:`HeuristicExtractor` — deterministic and model-free. One episodic entry
  per turn, plus promotion of stated user preferences to semantic and of how-to
  answers to procedural, using the language cues in
  :mod:`arcana.memory.extraction.signals`. This is the default: free, testable,
  and it ships value without a round-trip.
* :class:`LLMExtractor` — a single low-temperature gateway call returning a small
  JSON list. Any model or parse error falls back to the heuristic for that turn,
  so extraction can never crash a run.

Scoring/text helpers live in :mod:`arcana.memory.extraction.scoring`, tunables in
:mod:`arcana.memory.extraction.config`, and the LLM prompts in
:mod:`arcana.memory.extraction.prompts`.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, TypeAdapter, ValidationError, field_validator

from arcana.memory.extraction.config import (
    BASE_IMPORTANCE_EPISODIC,
    BASE_IMPORTANCE_PROCEDURAL,
    BASE_IMPORTANCE_SEMANTIC,
    DEFAULT_AGENT_CONFIDENCE_CAP,
    MAX_ENTRY_CONTENT,
    USER_CONFIRMED_CONFIDENCE,
    ExtractionConfig,
)
from arcana.memory.extraction.prompts import EXTRACT_SYSTEM, SUMMARISE_SYSTEM
from arcana.memory.extraction.scoring import (
    compute_importance,
    distill_semantic_clause,
    heuristic_summary,
    trim_content,
)
from arcana.memory.extraction.signals import (
    ENGLISH,
    SignalPatterns,
    has_durable_signal,
    has_steps,
    is_howto,
)
from arcana.models.adapters.base import CompletionRequest
from arcana.types import (
    ConfidenceSource,
    ExtractionStrategy,
    MemoryEntry,
    MemoryType,
    Session,
)

if TYPE_CHECKING:
    from arcana.models.gateway import ModelGateway

logger = logging.getLogger("arcana.memory.extraction")

# Per-field sub-limits scale with the (env-tunable) entry cap, so one knob keeps
# both a stored entry and its constituent parts bounded.
_EPISODIC_PROMPT_CHARS = MAX_ENTRY_CONTENT // 3
_EPISODIC_RESPONSE_CHARS = MAX_ENTRY_CONTENT // 2

# How much of a turn/transcript we *feed* the LLM extractor — an input budget for
# the call, distinct from how large a stored entry may be.
_LLM_INPUT_PROMPT_CHARS = 1500
_LLM_INPUT_RESPONSE_CHARS = 2500
_LLM_TRANSCRIPT_MSG_CHARS = 500


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


def _validate_agent_cap(cap: float) -> float:
    """Guard the anti-poisoning invariant at the constructor boundary.

    Mirrors the ``[0.0, 1.0)`` bound the config models enforce, so an extractor
    built directly (bypassing ``ExtractionConfig``) can never let agent-asserted
    text reach un-overridable full confidence.
    """
    if not 0.0 <= cap < 1.0:
        raise ValueError(f"agent_confidence_cap must be in [0.0, 1.0), got {cap!r}")
    return cap


@runtime_checkable
class MemoryExtractor(Protocol):
    """Turns an exchange (and a whole session) into memories."""

    @property
    def signals(self) -> SignalPatterns:
        """The language cue set this extractor detects with — drives consolidation typing."""
        ...

    async def extract(self, prompt: str, response: str, session: Session) -> list[MemoryEntry]: ...

    async def summarise(self, session: Session) -> str: ...


# ---------------------------------------------------------------------------
# Heuristic (default)
# ---------------------------------------------------------------------------


class HeuristicExtractor:
    """Deterministic, model-free extraction.

    Per turn it always records one ``EPISODIC`` entry (what happened), promotes a
    stated user preference/fact to ``SEMANTIC``, and — when the user asked a
    how-to and the answer contains steps — records a ``PROCEDURAL`` entry. Turn
    entries are agent-asserted, so confidence is capped; an explicit user
    statement is ``USER_CONFIRMED`` at higher confidence.

    ``signals`` selects the language cue set (English by default); pass another
    :class:`SignalPatterns` to extract in a different language.
    """

    def __init__(
        self,
        *,
        agent_confidence_cap: float = DEFAULT_AGENT_CONFIDENCE_CAP,
        signals: SignalPatterns = ENGLISH,
    ) -> None:
        self._cap = _validate_agent_cap(agent_confidence_cap)
        self._signals = signals

    @property
    def signals(self) -> SignalPatterns:
        return self._signals

    async def extract(self, prompt: str, response: str, session: Session) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = [self._episodic(prompt, response, session)]

        if has_durable_signal(prompt, self._signals):
            entries.append(self._semantic(prompt, session))

        if is_howto(prompt, self._signals) and has_steps(response, self._signals):
            entries.append(self._procedural(response, session))

        return entries

    async def summarise(self, session: Session) -> str:
        return heuristic_summary(session.messages)

    # -- entry builders -------------------------------------------------

    def _episodic(self, prompt: str, response: str, session: Session) -> MemoryEntry:
        content = (
            f"User asked: {trim_content(prompt, _EPISODIC_PROMPT_CHARS)}\n"
            f"Assistant replied: {trim_content(response, _EPISODIC_RESPONSE_CHARS)}"
        )
        return MemoryEntry(
            agent_id=session.agent_id,
            type=MemoryType.EPISODIC,
            content=content,
            importance=compute_importance(prompt, base=BASE_IMPORTANCE_EPISODIC, signals=self._signals),
            confidence=self._cap,
            confidence_source=ConfidenceSource.AGENT,
            source_session_id=session.id,
        )

    def _semantic(self, prompt: str, session: Session) -> MemoryEntry:
        return MemoryEntry(
            agent_id=session.agent_id,
            type=MemoryType.SEMANTIC,
            content=distill_semantic_clause(prompt, self._signals),
            importance=compute_importance(prompt, base=BASE_IMPORTANCE_SEMANTIC, signals=self._signals),
            # The user stated this — user-sourced and trusted above agent text.
            confidence=USER_CONFIRMED_CONFIDENCE,
            confidence_source=ConfidenceSource.USER_CONFIRMED,
            source_session_id=session.id,
        )

    def _procedural(self, response: str, session: Session) -> MemoryEntry:
        return MemoryEntry(
            agent_id=session.agent_id,
            type=MemoryType.PROCEDURAL,
            content=trim_content(response),
            importance=compute_importance(response, base=BASE_IMPORTANCE_PROCEDURAL, signals=self._signals),
            confidence=self._cap,
            confidence_source=ConfidenceSource.AGENT,
            source_session_id=session.id,
        )


# ---------------------------------------------------------------------------
# LLM (opt-in)
# ---------------------------------------------------------------------------


class LLMExtractor:
    """Low-temperature gateway extraction with a heuristic safety net.

    A single small completion returns a JSON list of candidate memories, each
    validated into a :class:`MemoryEntry` at agent-capped confidence (a model
    never asserts a fact at ``1.0``). Any gateway error, malformed JSON, or empty
    result falls back to :class:`HeuristicExtractor` for that turn, so extraction
    never fails a run.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        model: str,
        *,
        agent_confidence_cap: float = DEFAULT_AGENT_CONFIDENCE_CAP,
        temperature: float = 0.0,
        fallback: MemoryExtractor | None = None,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._cap = _validate_agent_cap(agent_confidence_cap)
        self._temperature = temperature
        self._fallback: MemoryExtractor = fallback or HeuristicExtractor(agent_confidence_cap=agent_confidence_cap)

    @property
    def signals(self) -> SignalPatterns:
        # Consolidation typing follows the same cues the heuristic fallback uses.
        return self._fallback.signals

    async def extract(self, prompt: str, response: str, session: Session) -> list[MemoryEntry]:
        try:
            raw = await self._complete(
                EXTRACT_SYSTEM,
                f"User:\n{trim_content(prompt, _LLM_INPUT_PROMPT_CHARS)}\n\n"
                f"Assistant:\n{trim_content(response, _LLM_INPUT_RESPONSE_CHARS)}",
            )
            entries = self._parse(raw, session)
        except Exception:
            logger.warning("LLM extraction failed; using heuristic fallback", exc_info=True)
            entries = []

        if not entries:
            return await self._fallback.extract(prompt, response, session)
        return entries

    async def summarise(self, session: Session) -> str:
        transcript = "\n".join(
            f"{m.role.value}: {trim_content(m.content, _LLM_TRANSCRIPT_MSG_CHARS)}" for m in session.messages
        )
        try:
            text = await self._complete(SUMMARISE_SYSTEM, transcript)
            text = text.strip()
            if text:
                return trim_content(text)
        except Exception:
            logger.warning("LLM summarisation failed; using heuristic fallback", exc_info=True)
        return heuristic_summary(session.messages)

    # -- internals ------------------------------------------------------

    async def _complete(self, system: str, user: str) -> str:
        request = CompletionRequest(
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self._temperature,
        )
        result = await self._gateway.complete(self._model, request)
        return result.content

    def _parse(self, raw: str, session: Session) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for item in _extract_json_array(raw):
            try:
                candidate = _ExtractedItem.model_validate(item)
            except ValidationError:
                continue  # skip a malformed element, keep the rest
            entries.append(
                MemoryEntry(
                    agent_id=session.agent_id,
                    type=candidate.type,
                    content=trim_content(candidate.content),
                    importance=candidate.importance,
                    # Model-generated → agent-sourced, capped. Never full confidence.
                    confidence=self._cap,
                    confidence_source=ConfidenceSource.AGENT,
                    source_session_id=session.id,
                )
            )
        return entries


_EXTRACTABLE_TYPES = frozenset({MemoryType.EPISODIC, MemoryType.SEMANTIC, MemoryType.PROCEDURAL})


class _ExtractedItem(BaseModel):
    """One candidate memory in the LLM extractor's JSON reply.

    A typed schema, so the model output is validated rather than duck-typed:
    ``type`` must coerce to an extractable :class:`MemoryType` (case-insensitive;
    ``preference`` and unknown labels are rejected), ``content`` must be
    non-empty, and ``importance`` is coerced and clamped to ``[0, 1]`` (defaulting
    when absent or non-numeric). A failure raises ``ValidationError`` and the
    caller skips that one element.
    """

    type: MemoryType
    content: str
    importance: float = BASE_IMPORTANCE_EPISODIC

    @field_validator("type", mode="before")
    @classmethod
    def _normalise_type(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("type")
    @classmethod
    def _only_turn_types(cls, value: MemoryType) -> MemoryType:
        if value not in _EXTRACTABLE_TYPES:
            raise ValueError(f"type {value!r} is not extractable")
        return value

    @field_validator("content")
    @classmethod
    def _require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content is empty")
        return value

    @field_validator("importance", mode="before")
    @classmethod
    def _clamp_importance(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            return BASE_IMPORTANCE_EPISODIC
        try:
            number = float(value)
        except ValueError:
            return BASE_IMPORTANCE_EPISODIC
        # NaN/inf slip past float() but defeat the clamp (min/max propagate NaN),
        # and MemoryEntry.importance has no numeric bound — reject non-finite here.
        if not math.isfinite(number):
            return BASE_IMPORTANCE_EPISODIC
        return round(min(max(number, 0.0), 1.0), 4)


# Validates that the model returned a JSON array and yields its elements typed as
# ``object`` — no ``cast`` needed; each element is then validated per-item above.
_JSON_ARRAY_ADAPTER: TypeAdapter[list[object]] = TypeAdapter(list[object])


def _extract_json_array(raw: str) -> list[object]:
    """Extract and parse a JSON array from a model reply, tolerating surrounding prose."""
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array in response")
    return _JSON_ARRAY_ADAPTER.validate_json(raw[start : end + 1])


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_extractor(
    config: ExtractionConfig,
    *,
    gateway: ModelGateway | None = None,
    model: str | None = None,
) -> MemoryExtractor:
    """Select an extractor from config, falling back to heuristic without a model.

    ``ExtractionStrategy.LLM`` needs both a gateway and a non-empty model; absent
    either (the no-provider case), extraction is forced to the deterministic
    heuristic.
    """
    if config.strategy == ExtractionStrategy.LLM and gateway is not None and model:
        return LLMExtractor(gateway, model, agent_confidence_cap=config.agent_confidence_cap)
    return HeuristicExtractor(agent_confidence_cap=config.agent_confidence_cap)
