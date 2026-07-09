"""LLM-backed extraction tests — the real-model counterpart to test_extraction.py.

Marked ``llm_eval``: these need a live model backend (Ollama by default), so they
are excluded from the default CI lane. They validate that extraction and
summarisation behave against genuine model output — in particular that
``trim_content`` keeps entries within ``MAX_ENTRY_CONTENT`` on real, long
responses, which is how you tune that cap. Run manually:

    uv run pytest packages/arcana-core/tests/memory/test_extraction_llm.py -m llm_eval -s -o addopts=""

Override the model with ``ARCANA_EXTRACTION_TEST_MODEL`` (e.g. ``ollama/llama3``).
Each test skips gracefully when no backend is reachable.
"""

import os
from collections.abc import AsyncIterator

import pytest

from arcana.memory.extraction import MAX_ENTRY_CONTENT, LLMExtractor
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.types import ConfidenceSource, MemoryType, MessageRole
from tests.support.factories import make_session as _session

pytestmark = pytest.mark.llm_eval

_MODEL = os.getenv("ARCANA_EXTRACTION_TEST_MODEL", "ollama/hermes-3")
_VALID_TYPES = {MemoryType.EPISODIC, MemoryType.SEMANTIC, MemoryType.PROCEDURAL}


@pytest.fixture
async def gateway(tmp_path) -> AsyncIterator[ModelGateway]:
    gw = ModelGateway(ConnectionStore(path=tmp_path / "models.json"))
    health = await gw.health(_MODEL)
    if not any(h.healthy for h in health.values()):
        await gw.aclose()
        pytest.skip(f"no reachable model backend for {_MODEL!r} — set ARCANA_EXTRACTION_TEST_MODEL or start Ollama")
    yield gw
    await gw.aclose()


async def test_llm_extract_returns_typed_capped_entries(gateway: ModelGateway):
    ext = LLMExtractor(gateway, _MODEL, agent_confidence_cap=0.7)
    prompt = "Remember that I prefer metric units and dark mode."
    response = "Understood — I'll use metric units and dark mode from now on."
    session = _session((MessageRole.USER, prompt))

    entries = await ext.extract(prompt, response, session)

    assert entries, "expected at least one extracted memory"
    for e in entries:
        assert e.type in _VALID_TYPES
        assert 0.0 <= e.importance <= 1.0
        assert len(e.content) <= MAX_ENTRY_CONTENT
        # A model never asserts a fact at full confidence.
        if e.confidence_source == ConfidenceSource.AGENT:
            assert e.confidence <= 0.7


async def test_llm_extract_trims_long_content(gateway: ModelGateway):
    """Real model output for a verbose turn still lands within the entry cap."""
    ext = LLMExtractor(gateway, _MODEL)
    prompt = "Explain the deployment process in exhaustive detail."
    response = "First, build the image. " * 200  # deliberately long
    session = _session((MessageRole.USER, prompt))

    entries = await ext.extract(prompt, response, session)

    assert entries
    for e in entries:
        assert len(e.content) <= MAX_ENTRY_CONTENT


async def test_llm_summarise_is_nonempty_and_bounded(gateway: ModelGateway):
    ext = LLMExtractor(gateway, _MODEL)
    session = _session(
        (
            MessageRole.USER,
            "My name is Priscila and I'm building Arcana, an agentic OS, so I can hire all of my friends.",
        ),
        (MessageRole.ASSISTANT, "Great to meet you, Priscila — Arcana sounds ambitious."),
    )

    summary = await ext.summarise(session)

    assert summary.strip(), "summary should not be empty"
    assert len(summary) <= MAX_ENTRY_CONTENT
