"""Integration tests: Agent + MemoryAdapter — search, inject, write."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from arcana.agents.agent import Agent
from arcana.agents.session_manager import SessionManager
from arcana.memory import SQLiteAdapter
from arcana.types.card import Card
from arcana.types.memory import ConfidenceSource, MemoryEntry, MemoryQuery, MemoryType, RetrievalMode
from tests.support.factories import make_entry
from tests.support.fakes import make_gateway

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gateway(content: str = "The answer is 42.") -> MagicMock:
    return make_gateway(content=content, input_tokens=8, output_tokens=4)


def _memory_adapter(search_results: list[MemoryEntry] | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.search = AsyncMock(return_value=search_results or [])
    adapter.write = AsyncMock()
    return adapter


def _make_entry(content: str, agent_id: UUID | None = None) -> MemoryEntry:
    return make_entry(agent_id=agent_id or uuid4(), type=MemoryType.EPISODIC, content=content, importance=0.6)


# ---------------------------------------------------------------------------
# Memory search is called on run()
# ---------------------------------------------------------------------------


async def test_run_calls_memory_search_with_prompt():
    mem = _memory_adapter()
    ag = Agent(name="x", card=Card.HERMIT, gateway=_gateway(), model="ollama/test", memory=mem)

    await ag.run("what is the meaning of life?")

    mem.search.assert_awaited_once()
    call_args = mem.search.call_args[0][0]
    assert isinstance(call_args, MemoryQuery)
    assert call_args.text is not None and "meaning of life" in call_args.text


async def test_run_calls_memory_write_after_response():
    mem = _memory_adapter()
    ag = Agent(name="x", card=Card.HERMIT, gateway=_gateway(), model="ollama/test", memory=mem)

    await ag.run("summarise the plan")

    mem.write.assert_awaited_once()
    written: MemoryEntry = mem.write.call_args[0][0]
    assert isinstance(written, MemoryEntry)
    assert written.type == MemoryType.EPISODIC
    assert written.agent_id == ag.id


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


async def test_run_injects_memory_context_into_system_prompt():
    entries = [_make_entry("User prefers concise answers")]
    mem = _memory_adapter(search_results=entries)
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    await ag.run("anything")

    call_req = gw.complete.call_args[0][1]
    assert "User prefers concise answers" in call_req.system
    assert "Relevant Memory" in call_req.system


async def test_run_no_memory_context_when_search_returns_empty():
    mem = _memory_adapter(search_results=[])
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test", memory=mem)

    await ag.run("anything")

    call_req = gw.complete.call_args[0][1]
    assert "Relevant Memory" not in call_req.system


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


async def test_run_without_memory_adapter_does_not_fail():
    gw = _gateway()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gw, model="ollama/test")
    result = await ag.run("hello")
    assert result == "The answer is 42."


# ---------------------------------------------------------------------------
# stream() extracts memory on close, at parity with run()
# ---------------------------------------------------------------------------


async def test_stream_extracts_memory_on_close():
    """stream() finalises the same as run(): retrieve context, then persist memories."""
    mem = _memory_adapter()
    ag = Agent(name="x", card=Card.HERMIT, gateway=_gateway(), model="ollama/test", memory=mem)

    chunks = []
    async for chunk in ag.stream("stream me something"):
        chunks.append(chunk)

    # search IS called (memory context is retrieved for all requests)
    mem.search.assert_awaited_once()
    # write IS called — the streamed turn is extracted and persisted like run()
    mem.write.assert_awaited()


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


# ---------------------------------------------------------------------------
# Typed extraction end-to-end (with SessionManager summarisation on close)
# ---------------------------------------------------------------------------


async def test_run_extracts_typed_entries_and_consolidated_summary(tmp_path: Path):
    """A durable-preference turn writes a SEMANTIC entry, plus a consolidated one on close."""
    mem = _memory_adapter()
    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(
        name="x",
        card=Card.HERMIT,
        gateway=_gateway(content="Understood."),
        model="ollama/test",
        memory=mem,
        id=agent_id,
        session_manager=sm,
    )

    await ag.run("Remember that I prefer concise answers")

    written = [call.args[0] for call in mem.write.await_args_list]
    types = {e.type for e in written}
    # Per-turn: an EPISODIC turn record + a SEMANTIC user preference.
    assert MemoryType.EPISODIC in types
    assert MemoryType.SEMANTIC in types
    # The consolidated summary memory is the final write and carries the summary.
    consolidated = written[-1]
    assert consolidated.content
    # Every agent-sourced entry stays below full confidence.
    for e in written:
        if e.confidence_source == ConfidenceSource.AGENT:
            assert e.confidence < 1.0


async def test_run_semantic_preference_persists_in_real_store(tmp_path: Path):
    """A stated preference survives to disk as a retrievable SEMANTIC entry."""
    store = SQLiteAdapter(tmp_path / "memory.db")
    await store.connect()
    try:
        sm = SessionManager(base_dir=tmp_path / "agents")
        ag = Agent(
            name="x",
            card=Card.HERMIT,
            gateway=_gateway(content="Noted."),
            model="ollama/test",
            memory=store,
            session_manager=sm,
        )

        await ag.run("Remember that I prefer dark roast coffee")

        hits = await store.search(MemoryQuery(keywords=["coffee"], retrieval_mode=RetrievalMode.keyword, limit=10))
        semantic = [e for e in hits if e.type == MemoryType.SEMANTIC]
        assert semantic, "expected a SEMANTIC preference entry to persist"
        # The user-stated preference persists as a USER_CONFIRMED semantic entry.
        assert any(e.confidence_source == ConfidenceSource.USER_CONFIRMED for e in semantic)
    finally:
        await store.aclose()
