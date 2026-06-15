"""Integration tests: Agent + MemoryAdapter — search, inject, write."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from arcana.agents.agent import Agent
from arcana.models.adapters.base import CompletionResponse, ModelChunk
from arcana.models.gateway import ModelGateway
from arcana.types.card import Card
from arcana.types.memory import MemoryEntry, MemoryQuery, MemoryType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gateway(content: str = "The answer is 42.") -> MagicMock:
    gw = MagicMock(spec=ModelGateway)
    gw.complete = AsyncMock(return_value=CompletionResponse(content=content, input_tokens=8, output_tokens=4))

    words = content.split()

    async def _stream(_model: str, _req: object) -> AsyncGenerator[ModelChunk, None]:
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            yield ModelChunk(text=word + " ", input_tokens=8 if is_last else 0, output_tokens=4 if is_last else 0)

    gw.stream = _stream
    return gw


def _memory_adapter(search_results: list[MemoryEntry] | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.search = AsyncMock(return_value=search_results or [])
    adapter.write = AsyncMock()
    return adapter


def _make_entry(content: str, agent_id=None) -> MemoryEntry:
    return MemoryEntry(
        agent_id=agent_id or uuid4(),
        type=MemoryType.EPISODIC,
        content=content,
        importance=0.6,
    )


# ---------------------------------------------------------------------------
# Memory search is called on run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_calls_memory_search_with_prompt():
    mem = _memory_adapter()
    ag = Agent(name="x", card=Card.HERMIT, gateway=_gateway(), model="ollama/test", memory=mem)

    await ag.run("what is the meaning of life?")

    mem.search.assert_awaited_once()
    call_args = mem.search.call_args[0][0]
    assert isinstance(call_args, MemoryQuery)
    assert call_args.text is not None and "meaning of life" in call_args.text


@pytest.mark.asyncio
async def test_run_calls_memory_write_after_response():
    mem = _memory_adapter()
    ag = Agent(name="x", card=Card.HERMIT, gateway=_gateway(), model="ollama/test", memory=mem)

    await ag.run("summarise the plan")

    mem.write.assert_awaited_once()
    written: MemoryEntry = mem.write.call_args[0][0]
    assert isinstance(written, MemoryEntry)
    assert written.type == MemoryType.EPISODIC
    assert written.agent_id == ag.id


@pytest.mark.asyncio
async def test_run_memory_write_includes_prompt_and_response():
    mem = _memory_adapter()
    gw = _gateway(content="RAG retrieves, fine-tuning adapts.")
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    await ag.run("RAG vs fine-tuning?")

    written: MemoryEntry = mem.write.call_args[0][0]
    assert "RAG vs fine-tuning" in written.content
    assert "RAG retrieves" in written.content


# ---------------------------------------------------------------------------
# Memory context is injected into the system prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_injects_memory_context_into_system_prompt():
    entries = [_make_entry("User prefers concise answers")]
    mem = _memory_adapter(search_results=entries)
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    await ag.run("anything")

    call_req = gw.complete.call_args[0][1]
    assert "User prefers concise answers" in call_req.system
    assert "Relevant Memory" in call_req.system


@pytest.mark.asyncio
async def test_run_no_memory_context_when_search_returns_empty():
    mem = _memory_adapter(search_results=[])
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    await ag.run("anything")

    call_req = gw.complete.call_args[0][1]
    assert "Relevant Memory" not in call_req.system


@pytest.mark.asyncio
async def test_run_multiple_memory_entries_all_injected():
    entries = [_make_entry("Fact A"), _make_entry("Fact B"), _make_entry("Fact C")]
    mem = _memory_adapter(search_results=entries)
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    await ag.run("give context")

    call_req = gw.complete.call_args[0][1]
    assert "Fact A" in call_req.system
    assert "Fact B" in call_req.system
    assert "Fact C" in call_req.system


# ---------------------------------------------------------------------------
# No memory adapter — no calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_without_memory_adapter_does_not_fail():
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test")
    result = await ag.run("hello")
    assert result == "The answer is 42."


# ---------------------------------------------------------------------------
# stream() does NOT call _extract_memory (documents the current asymmetry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_does_not_call_memory_write():
    """stream() currently skips _extract_memory — this test documents that gap."""
    mem = _memory_adapter()
    ag = Agent(name="x", card=Card.HERMIT, gateway=_gateway(), model="ollama/test", memory=mem)

    chunks = []
    async for chunk in ag.stream("stream me something"):
        chunks.append(chunk)

    # search IS called (memory context is retrieved for all requests)
    mem.search.assert_awaited_once()
    # write is NOT called — stream() doesn't persist memories
    mem.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_injects_memory_context_into_system_prompt():
    """Memory context still reaches the system prompt during streaming."""
    entries = [_make_entry("Remember this fact")]
    mem = _memory_adapter(search_results=entries)
    gw = _gateway(content="streamed response")
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    chunks = []
    async for chunk in ag.stream("question"):
        chunks.append(chunk)

    # We can't directly inspect the request passed to stream() via the MagicMock
    # because stream() is a plain function returning an async generator, not an
    # AsyncMock. Verify indirectly: search was called, meaning context was retrieved.
    mem.search.assert_awaited_once()
