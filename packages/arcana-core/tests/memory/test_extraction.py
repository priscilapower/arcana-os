"""Unit tests for memory extraction — heuristic + LLM strategies. No live LLM."""

import math
import re
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from arcana.memory.extraction import (
    DEFAULT_AGENT_CONFIDENCE_CAP,
    MAX_ENTRY_CONTENT,
    USER_CONFIRMED_CONFIDENCE,
    ExtractionConfig,
    HeuristicExtractor,
    LLMExtractor,
    build_consolidated_entry,
    build_extractor,
    compute_importance,
    filter_storable,
    heuristic_summary,
    trim_content,
)
from arcana.memory.extraction.config import BASE_IMPORTANCE_EPISODIC, ExtractionTunables
from arcana.memory.extraction.signals import (
    ENGLISH,
    SignalPatterns,
    get_signals,
    has_durable_signal,
    register_language,
)
from arcana.models.adapters.base import CompletionResponse
from arcana.models.gateway import ModelGateway
from arcana.types import ConfidenceSource, ExtractionStrategy, MemoryEntry, MemoryType, MessageRole, Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(*turns: tuple[MessageRole, str]) -> Session:
    session = Session(agent_id=uuid4())
    for role, content in turns:
        session.add_message(role, content)
    return session


def _gateway(content: str) -> MagicMock:
    gw = MagicMock(spec=ModelGateway)
    gw.complete = AsyncMock(return_value=CompletionResponse(content=content, input_tokens=5, output_tokens=5))
    return gw


# ---------------------------------------------------------------------------
# HeuristicExtractor — determinism & typing
# ---------------------------------------------------------------------------


async def test_heuristic_records_one_episodic_per_plain_turn():
    ext = HeuristicExtractor()
    session = _session((MessageRole.USER, "What is RAG?"))

    entries = await ext.extract("What is RAG?", "RAG retrieves documents.", session)

    assert len(entries) == 1
    (episodic,) = entries
    assert episodic.type == MemoryType.EPISODIC
    assert "What is RAG?" in episodic.content
    assert "RAG retrieves documents." in episodic.content
    assert episodic.source_session_id == session.id
    assert episodic.agent_id == session.agent_id


async def test_heuristic_is_deterministic():
    ext = HeuristicExtractor()
    session = _session((MessageRole.USER, "Remember that I prefer tea"))

    first = await ext.extract("Remember that I prefer tea", "Noted.", session)
    second = await ext.extract("Remember that I prefer tea", "Noted.", session)

    def fingerprint(entries: list[MemoryEntry]) -> list[tuple[object, ...]]:
        return [(e.type, e.content, e.importance, e.confidence, e.confidence_source) for e in entries]

    assert fingerprint(first) == fingerprint(second)


async def test_heuristic_promotes_user_preference_to_semantic():
    ext = HeuristicExtractor()
    session = _session((MessageRole.USER, "Remember that I prefer concise answers"))

    entries = await ext.extract("Remember that I prefer concise answers", "Understood.", session)

    semantic = [e for e in entries if e.type == MemoryType.SEMANTIC]
    assert len(semantic) == 1
    assert semantic[0].confidence_source == ConfidenceSource.USER_CONFIRMED
    assert semantic[0].confidence == USER_CONFIRMED_CONFIDENCE


async def test_heuristic_promotes_howto_answer_to_procedural():
    ext = HeuristicExtractor()
    prompt = "How do I deploy the service?"
    response = "1. Build the image\n2. Push it\n3. Roll out"
    session = _session((MessageRole.USER, prompt))

    entries = await ext.extract(prompt, response, session)

    procedural = [e for e in entries if e.type == MemoryType.PROCEDURAL]
    assert len(procedural) == 1
    assert procedural[0].confidence_source == ConfidenceSource.AGENT


# ---------------------------------------------------------------------------
# Confidence cap + storable filter
# ---------------------------------------------------------------------------


async def test_agent_sourced_confidence_is_capped_below_one():
    ext = HeuristicExtractor(agent_confidence_cap=0.7)
    session = _session((MessageRole.USER, "hello"))

    entries = await ext.extract("hello", "hi there", session)

    episodic = next(e for e in entries if e.type == MemoryType.EPISODIC)
    assert episodic.confidence_source == ConfidenceSource.AGENT
    assert episodic.confidence == 0.7
    assert episodic.confidence < 1.0


@pytest.mark.parametrize("cap", [1.0, 1.5, -0.1])
def test_extractor_constructors_reject_out_of_range_cap(cap: float):
    """The invariant holds even when an extractor is built directly, bypassing config."""
    with pytest.raises(ValueError, match="agent_confidence_cap"):
        HeuristicExtractor(agent_confidence_cap=cap)
    with pytest.raises(ValueError, match="agent_confidence_cap"):
        LLMExtractor(_gateway("[]"), "ollama/test", agent_confidence_cap=cap)


def test_filter_storable_drops_below_threshold():
    agent = uuid4()
    weak = MemoryEntry(agent_id=agent, type=MemoryType.EPISODIC, content="weak", confidence=0.2)
    strong = MemoryEntry(agent_id=agent, type=MemoryType.EPISODIC, content="strong", confidence=0.5)

    kept = filter_storable([weak, strong], 0.3)

    assert kept == [strong]


# ---------------------------------------------------------------------------
# Importance heuristics
# ---------------------------------------------------------------------------


def test_importance_bumps_on_imperative_language():
    plain = compute_importance("what is the weather", base=0.4)
    emphatic = compute_importance("remember this always", base=0.4)
    assert plain == 0.4
    assert emphatic == 0.6


def test_importance_bumps_on_pin_and_caps_at_one():
    assert compute_importance("remember this", base=0.9, pinned=True) == 1.0


# ---------------------------------------------------------------------------
# Summary + consolidated entry
# ---------------------------------------------------------------------------


def test_heuristic_summary_uses_first_ask_and_last_reply():
    session = _session(
        (MessageRole.USER, "first question"),
        (MessageRole.ASSISTANT, "first answer"),
        (MessageRole.USER, "second question"),
        (MessageRole.ASSISTANT, "final answer"),
    )
    summary = heuristic_summary(session.messages)
    assert "first question" in summary
    assert "final answer" in summary
    assert "first answer" not in summary


def test_consolidated_entry_is_semantic_when_durable_signal_present():
    session = _session((MessageRole.USER, "Remember that I prefer dark mode"))
    session.summary = "User prefers dark mode."

    entry = build_consolidated_entry(session)

    assert entry is not None
    assert entry.type == MemoryType.SEMANTIC
    assert entry.importance == 0.7
    assert entry.confidence == DEFAULT_AGENT_CONFIDENCE_CAP


def test_consolidated_entry_is_episodic_without_durable_signal():
    session = _session((MessageRole.USER, "what time is it"))
    session.summary = "User asked for the time."

    entry = build_consolidated_entry(session)

    assert entry is not None
    assert entry.type == MemoryType.EPISODIC


def test_consolidated_entry_is_none_without_summary():
    session = _session((MessageRole.USER, "hi"))
    assert build_consolidated_entry(session) is None


# ---------------------------------------------------------------------------
# LLMExtractor — valid, malformed, exception
# ---------------------------------------------------------------------------


async def test_llm_extractor_parses_valid_json():
    gw = _gateway('[{"type": "semantic", "content": "User likes tea", "importance": 0.8}]')
    ext = LLMExtractor(gw, "ollama/test", agent_confidence_cap=0.7)
    session = _session((MessageRole.USER, "I like tea"))

    entries = await ext.extract("I like tea", "Noted.", session)

    assert len(entries) == 1
    assert entries[0].type == MemoryType.SEMANTIC
    assert entries[0].content == "User likes tea"
    assert entries[0].importance == 0.8
    # A model never asserts a fact at 1.0 — capped and AGENT-sourced.
    assert entries[0].confidence == 0.7
    assert entries[0].confidence_source == ConfidenceSource.AGENT


async def test_llm_extractor_rejects_non_finite_importance():
    """A model emitting NaN/inf importance must not produce a non-finite entry."""
    gw = _gateway('[{"type": "episodic", "content": "happened", "importance": "nan"}]')
    ext = LLMExtractor(gw, "ollama/test")
    session = _session((MessageRole.USER, "what happened"))

    entries = await ext.extract("what happened", "a thing", session)

    assert len(entries) == 1
    # Non-finite is clamped back to the episodic baseline, never stored as NaN.
    assert math.isfinite(entries[0].importance)
    assert entries[0].importance == BASE_IMPORTANCE_EPISODIC


async def test_llm_extractor_falls_back_to_heuristic_on_malformed_json():
    gw = _gateway("this is not json at all")
    ext = LLMExtractor(gw, "ollama/test")
    session = _session((MessageRole.USER, "hello"))

    entries = await ext.extract("hello", "hi there", session)

    # Fell back to the heuristic → a single agent-sourced episodic turn entry.
    assert len(entries) == 1
    assert entries[0].type == MemoryType.EPISODIC
    assert "hello" in entries[0].content
    gw.complete.assert_awaited_once()


async def test_llm_extractor_falls_back_to_heuristic_on_gateway_error():
    gw = MagicMock(spec=ModelGateway)
    gw.complete = AsyncMock(side_effect=RuntimeError("model down"))
    ext = LLMExtractor(gw, "ollama/test")
    session = _session((MessageRole.USER, "hello"))

    entries = await ext.extract("hello", "hi", session)

    assert len(entries) == 1
    assert entries[0].type == MemoryType.EPISODIC


async def test_llm_summarise_falls_back_to_heuristic_on_error():
    gw = MagicMock(spec=ModelGateway)
    gw.complete = AsyncMock(side_effect=RuntimeError("model down"))
    ext = LLMExtractor(gw, "ollama/test")
    session = _session((MessageRole.USER, "first"), (MessageRole.ASSISTANT, "last"))

    summary = await ext.summarise(session)

    assert "first" in summary and "last" in summary


# ---------------------------------------------------------------------------
# Factory / no-provider fallback
# ---------------------------------------------------------------------------


def test_build_extractor_defaults_to_heuristic():
    ext = build_extractor(ExtractionConfig())
    assert isinstance(ext, HeuristicExtractor)


def test_build_extractor_llm_without_model_falls_back_to_heuristic():
    ext = build_extractor(ExtractionConfig(strategy=ExtractionStrategy.LLM), gateway=_gateway("[]"), model=None)
    assert isinstance(ext, HeuristicExtractor)


def test_build_extractor_llm_with_model_selects_llm():
    ext = build_extractor(
        ExtractionConfig(strategy=ExtractionStrategy.LLM), gateway=_gateway("[]"), model="ollama/test"
    )
    assert isinstance(ext, LLMExtractor)


# ---------------------------------------------------------------------------
# Strategy enum
# ---------------------------------------------------------------------------


def test_extraction_config_default_strategy_is_heuristic():
    assert ExtractionConfig().strategy is ExtractionStrategy.HEURISTIC


def test_extraction_config_coerces_strategy_string_to_enum():
    # The raw-string path config.json takes — Pydantic coerces "llm" to the enum.
    assert ExtractionConfig.model_validate({"strategy": "llm"}).strategy is ExtractionStrategy.LLM


# ---------------------------------------------------------------------------
# trim_content — the per-entry content cap
# ---------------------------------------------------------------------------


def test_trim_content_collapses_whitespace():
    assert trim_content("a   b\n\n c\t d") == "a b c d"


def test_trim_content_under_limit_is_unchanged():
    assert trim_content("short text", 100) == "short text"


def test_trim_content_at_exact_limit_is_unchanged():
    assert trim_content("abcde", 5) == "abcde"


def test_trim_content_over_limit_truncates_with_ellipsis():
    out = trim_content("abcdefghij", 5)
    assert out.endswith("…")
    assert len(out) <= 5


def test_trim_content_empty_string():
    assert trim_content("") == ""


def test_trim_content_never_exceeds_limit():
    out = trim_content("word " * 500, 50)
    assert len(out) <= 50


def test_trim_content_default_limit_is_the_entry_cap():
    out = trim_content("x " * (MAX_ENTRY_CONTENT * 2))
    assert len(out) <= MAX_ENTRY_CONTENT


# ---------------------------------------------------------------------------
# Env-overridable tunables (pydantic-settings)
# ---------------------------------------------------------------------------


def test_tunables_defaults():
    tunables = ExtractionTunables()
    assert tunables.agent_confidence_cap == 0.7
    assert tunables.max_content == 600


def test_tunables_read_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_EXTRACTION_AGENT_CONFIDENCE_CAP", "0.55")
    monkeypatch.setenv("ARCANA_EXTRACTION_MAX_CONTENT", "1200")
    tunables = ExtractionTunables()
    assert tunables.agent_confidence_cap == 0.55
    assert tunables.max_content == 1200


def test_tunables_reject_malformed_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_EXTRACTION_MAX_CONTENT", "not-an-int")
    with pytest.raises(ValidationError):
        ExtractionTunables()


def test_tunables_ignore_unrelated_prefixed_env(monkeypatch: pytest.MonkeyPatch):
    # A prefixed var that isn't a knob (the LLM tests use this) must not raise.
    monkeypatch.setenv("ARCANA_EXTRACTION_TEST_MODEL", "ollama/whatever")
    assert ExtractionTunables().max_content == 600


def test_agent_confidence_cap_cannot_reach_full_confidence(monkeypatch: pytest.MonkeyPatch):
    """A cap of 1.0 would defeat the anti-poisoning invariant — reject it at load.

    Enforced at both config boundaries: the env-backed tunables and the
    ``config.json`` sub-block. Just-below-1.0 stays legal.
    """
    monkeypatch.setenv("ARCANA_EXTRACTION_AGENT_CONFIDENCE_CAP", "1.0")
    with pytest.raises(ValidationError):
        ExtractionTunables()

    with pytest.raises(ValidationError):
        ExtractionConfig(agent_confidence_cap=1.0)

    assert ExtractionConfig(agent_confidence_cap=0.99).agent_confidence_cap == 0.99


# ---------------------------------------------------------------------------
# Language signals (i18n)
# ---------------------------------------------------------------------------


def test_english_signals_detect_and_reject():
    assert has_durable_signal("I prefer tea")
    assert not has_durable_signal("what is the weather today")


def _portuguese_signals() -> SignalPatterns:
    return SignalPatterns(
        imperative=re.compile(r"\b(lembre|sempre|nunca)\b", re.IGNORECASE),
        preference=re.compile(r"\beu (prefiro|gosto|odeio)\b", re.IGNORECASE),
        howto=re.compile(r"\bcomo (faço|posso)\b", re.IGNORECASE),
        steps=re.compile(r"(?:^|\n)\s*\d+[.)]", re.IGNORECASE),
    )


def test_durable_signal_respects_injected_language():
    pt = _portuguese_signals()
    assert has_durable_signal("Eu prefiro chá", pt)
    # English patterns don't fire on Portuguese.
    assert not has_durable_signal("Eu prefiro chá")


def test_register_and_get_language_with_english_fallback():
    pt = _portuguese_signals()
    register_language("pt-test", pt)
    assert get_signals("pt-test") is pt
    assert get_signals("unknown-lang") is ENGLISH


async def test_heuristic_extractor_uses_injected_language_signals():
    ext = HeuristicExtractor(signals=_portuguese_signals())
    session = _session((MessageRole.USER, "Eu prefiro respostas curtas"))

    entries = await ext.extract("Eu prefiro respostas curtas", "Certo.", session)

    assert any(e.type == MemoryType.SEMANTIC for e in entries)


@pytest.mark.parametrize(
    "text",
    ["I'm confused about this", "I am wondering what to do", "I'm going to ask something"],
)
def test_conversational_filler_is_not_a_durable_signal(text: str):
    """Bare "I'm…/I am…" filler must not promote to a high-confidence semantic memory."""
    assert not has_durable_signal(text)


@pytest.mark.parametrize(
    "text",
    ["I'm a developer", "I am based in Berlin", "I'm using dark mode"],
)
def test_self_identification_is_a_durable_signal(text: str):
    """Determiner/role-gated self-identification still counts as durable."""
    assert has_durable_signal(text)


def test_consolidated_entry_respects_injected_language_signals():
    """A Portuguese durable statement consolidates as SEMANTIC under PT signals,
    but is only EPISODIC under the default English cues."""
    pt = _portuguese_signals()
    session = _session((MessageRole.USER, "Eu prefiro modo escuro"))
    session.summary = "Usuário prefere modo escuro."

    under_pt = build_consolidated_entry(session, signals=pt)
    under_default = build_consolidated_entry(session)
    assert under_pt is not None and under_pt.type == MemoryType.SEMANTIC
    assert under_default is not None and under_default.type == MemoryType.EPISODIC
