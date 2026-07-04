"""Tests for ``build_federation`` — the memory assembly seam.

Covers what the assembly helper is responsible for: constructing the private
(and optional global) tiers on disk, running the private store's migrations,
returning a protocol-valid federation, degrading to SQLite-only without an
embedding provider, and closing its connections on teardown. No LLM calls.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import pytest

from arcana.memory import (
    EmbeddingGateway,
    MemoryConfig,
    MemoryFederation,
    MemoryRoutingError,
    build_federation,
    load_memory_config,
)
from arcana.memory.migrations import latest_version
from arcana.types import MemoryEntry, MemoryQuery, MemoryScope, MemoryType


def _entry(
    agent_id: UUID,
    *,
    content: str = "alpha",
    scope: MemoryScope = MemoryScope.PRIVATE,
    importance: float = 0.5,
) -> MemoryEntry:
    return MemoryEntry(
        agent_id=agent_id,
        type=MemoryType.SEMANTIC,
        content=content,
        scope=scope,
        importance=importance,
    )


# --------------------------------------------------------------------------
# Tier construction + persistence
# --------------------------------------------------------------------------


async def test_build_federation_returns_a_protocol_valid_adapter(tmp_path: Path):
    fed = await build_federation(uuid4(), home=tmp_path)
    assert isinstance(fed, MemoryFederation)
    # The MemoryAdapter surface an Agent depends on.
    assert callable(fed.search) and callable(fed.write) and callable(fed.health_check)
    health = await fed.health_check()
    assert health.healthy
    await fed.aclose()


async def test_build_federation_persists_private_store_at_agent_path(tmp_path: Path):
    agent_id = uuid4()
    fed = await build_federation(agent_id, home=tmp_path)

    entry = _entry(agent_id, content="the sky is alpha")
    await fed.write(entry)
    got = await fed.search(MemoryQuery(agent_id=agent_id, text="alpha"))
    assert [e.id for e in got] == [entry.id]

    # PRIVATE tier lives beside the agent's other state.
    assert (tmp_path / "agents" / str(agent_id) / "memory.db").exists()
    await fed.aclose()


async def test_build_federation_runs_private_migrations(tmp_path: Path):
    agent_id = uuid4()
    fed = await build_federation(agent_id, home=tmp_path)
    await fed.aclose()

    # Opening the store migrated it to the latest schema version.
    db = tmp_path / "agents" / str(agent_id) / "memory.db"
    async with aiosqlite.connect(db) as conn:
        row = await (await conn.execute("PRAGMA user_version")).fetchone()
    assert row is not None and row[0] == latest_version()


# --------------------------------------------------------------------------
# Embedding-absent degradation vs. embedding-present global tier
# --------------------------------------------------------------------------


async def test_without_embedding_the_global_tier_is_disabled(tmp_path: Path):
    agent_id = uuid4()
    fed = await build_federation(agent_id, home=tmp_path, embedding=None)

    # No global backend registered: a GLOBAL-scoped write has nowhere to route.
    with pytest.raises(MemoryRoutingError):
        await fed.write(_entry(agent_id, scope=MemoryScope.GLOBAL))

    # No global vector store is created on disk.
    assert not (tmp_path / "vector" / "global.db").exists()
    await fed.aclose()


async def test_with_embedding_the_global_tier_is_wired(tmp_path: Path):
    agent_id = uuid4()
    # An empty gateway resolves to no embedder, so the vector tier degrades to
    # keyword storage — enough to prove the GLOBAL tier is wired and routable.
    fed = await build_federation(agent_id, home=tmp_path, embedding=EmbeddingGateway([]))

    entry = _entry(agent_id, content="shared alpha fact", scope=MemoryScope.GLOBAL)
    await fed.write(entry)
    got = await fed.search(MemoryQuery(text="alpha", scope=MemoryScope.GLOBAL))
    assert [e.id for e in got] == [entry.id]

    assert (tmp_path / "vector" / "global.db").exists()
    await fed.aclose()


# --------------------------------------------------------------------------
# Teardown
# --------------------------------------------------------------------------


async def test_aclose_closes_the_private_connection_and_is_idempotent(tmp_path: Path):
    agent_id = uuid4()
    fed = await build_federation(agent_id, home=tmp_path)
    await fed.write(_entry(agent_id))

    private = fed._router.all_tiers()[0].adapter.inner  # unwrap the resilience wrapper
    assert private._conn is not None

    await fed.aclose()
    assert private._conn is None
    await fed.aclose()  # second close is a no-op, not an error


# --------------------------------------------------------------------------
# Config loader
# --------------------------------------------------------------------------


def test_load_memory_config_defaults_when_file_absent(tmp_path: Path):
    cfg = load_memory_config(tmp_path)
    assert cfg == MemoryConfig()
    assert cfg.enabled is True
    assert cfg.global_ == "vector"


def test_load_memory_config_defaults_when_block_absent(tmp_path: Path):
    (tmp_path / "config.json").write_text(json.dumps({"version": "0.1.0"}))
    assert load_memory_config(tmp_path) == MemoryConfig()


def test_load_memory_config_reads_the_memory_block(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"memory": {"enabled": False, "private": "sqlite", "global": "vector", "pools": ["team"]}})
    )
    cfg = load_memory_config(tmp_path)
    assert cfg.enabled is False
    assert cfg.pools == ["team"]
    assert cfg.global_ == "vector"


def test_load_memory_config_survives_malformed_json(tmp_path: Path):
    (tmp_path / "config.json").write_text("{not valid json")
    assert load_memory_config(tmp_path) == MemoryConfig()
