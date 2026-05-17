"""Tests for the SQLite memory adapter."""

import asyncio
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.types.memory import MemoryEntry, MemoryQuery, MemoryType


@pytest.fixture
async def adapter(tmp_path):
    db_path = tmp_path / "test_memory.db"
    adapter = SQLiteAdapter(db_path=db_path)
    await adapter.connect()
    yield adapter
    await adapter.close()


@pytest.mark.asyncio
async def test_write_and_read(adapter):
    agent_id = uuid4()
    entry = MemoryEntry(
        agent_id=agent_id,
        type=MemoryType.SEMANTIC,
        content="RAG stands for Retrieval Augmented Generation",
        importance=0.8,
    )
    entry_id = await adapter.write(entry)
    result = await adapter.read(entry_id)
    assert result is not None
    assert result.content == entry.content
    assert result.importance == 0.8


@pytest.mark.asyncio
async def test_forget(adapter):
    entry = MemoryEntry(
        agent_id=uuid4(),
        type=MemoryType.EPISODIC,
        content="A temporary memory",
    )
    entry_id = await adapter.write(entry)
    await adapter.forget(entry_id)
    result = await adapter.read(entry_id)
    assert result is None


@pytest.mark.asyncio
async def test_search_by_agent(adapter):
    agent_id = uuid4()
    other_id = uuid4()

    for i in range(3):
        await adapter.write(MemoryEntry(
            agent_id=agent_id,
            type=MemoryType.SEMANTIC,
            content=f"Memory {i} for agent",
        ))
    await adapter.write(MemoryEntry(
        agent_id=other_id,
        type=MemoryType.SEMANTIC,
        content="Another agent's memory",
    ))

    results = await adapter.search(MemoryQuery(agent_id=agent_id, limit=10))
    assert len(results) == 3
    assert all(str(r.agent_id) == str(agent_id) for r in results)


@pytest.mark.asyncio
async def test_search_by_keyword(adapter):
    agent_id = uuid4()
    await adapter.write(MemoryEntry(
        agent_id=agent_id,
        type=MemoryType.SEMANTIC,
        content="vector databases are useful for semantic search",
    ))
    await adapter.write(MemoryEntry(
        agent_id=agent_id,
        type=MemoryType.SEMANTIC,
        content="the weather was nice today",
    ))

    results = await adapter.search(MemoryQuery(
        agent_id=agent_id,
        keywords=["vector"],
        limit=10,
    ))
    assert len(results) == 1
    assert "vector" in results[0].content


@pytest.mark.asyncio
async def test_health_check(adapter):
    health = await adapter.health_check()
    assert health.healthy is True
