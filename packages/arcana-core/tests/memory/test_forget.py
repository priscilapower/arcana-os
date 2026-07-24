"""Tests for the delete seam: adapter.delete/get + MemoryFederation.forget. No LLM."""

from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import (
    GlobalDeleteRefused,
    MemoryFederation,
    MemoryRouter,
    ReadOnlyTierDelete,
    SQLiteAdapter,
)
from arcana.memory.adapters.markdown import MarkdownFolderAdapter
from arcana.types import MemoryEntry, MemoryQuery, MemoryScope, RetrievalMode
from tests.support.factories import make_entry

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
async def adapter(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "memory.db")
    await a.connect()
    yield a
    await a.aclose()


def _entry(**overrides: object) -> MemoryEntry:
    return make_entry(**{"content": "the sky is blue", **overrides})


async def _keyword_hits(adapter: SQLiteAdapter, text: str) -> list[MemoryEntry]:
    return await adapter.search(MemoryQuery(text=text, retrieval_mode=RetrievalMode.keyword))


# --------------------------------------------------------------------------
# SQLiteAdapter.get / delete
# --------------------------------------------------------------------------


async def test_get_resolves_by_id_ignoring_filters(adapter: SQLiteAdapter):
    entry = _entry(importance=0.0, scope=MemoryScope.SHARED, pool_name="p")
    await adapter.write(entry)
    got = await adapter.get(entry.id)
    assert got is not None and got.id == entry.id


async def test_get_returns_none_for_unknown_id(adapter: SQLiteAdapter):
    assert await adapter.get(uuid4()) is None


async def test_get_resolves_archived_entry(adapter: SQLiteAdapter):
    entry = _entry()
    await adapter.write(entry)
    await adapter.delete(entry.id, hard=False)  # archive
    got = await adapter.get(entry.id)
    assert got is not None and got.archived is True


async def test_hard_delete_removes_row_and_fts(adapter: SQLiteAdapter):
    entry = _entry(content="alpha unique token")
    await adapter.write(entry)
    assert await _keyword_hits(adapter, "alpha") != []

    assert await adapter.delete(entry.id) is True
    assert await adapter.get(entry.id) is None
    # FTS row is gone via the delete trigger — keyword search no longer matches.
    assert await _keyword_hits(adapter, "alpha") == []


async def test_delete_unknown_id_returns_false(adapter: SQLiteAdapter):
    assert await adapter.delete(uuid4()) is False


async def test_soft_delete_archives_without_removing(adapter: SQLiteAdapter):
    entry = _entry()
    await adapter.write(entry)
    assert await adapter.delete(entry.id, hard=False) is True
    row = await adapter.get(entry.id)
    assert row is not None and row.archived is True
    # Hidden from a normal search, but the row still exists.
    assert await adapter.search(MemoryQuery(agent_id=entry.agent_id)) == []


# --------------------------------------------------------------------------
# MemoryFederation.forget — tier resolution + invariants
# --------------------------------------------------------------------------


@pytest.fixture
async def federation(tmp_path: Path):
    private = SQLiteAdapter(tmp_path / "private.db")
    await private.connect()
    global_ = SQLiteAdapter(tmp_path / "global.db", quick_check_on_open=False)
    await global_.connect()
    router = MemoryRouter(private=private, global_=global_)
    fed = MemoryFederation(router)
    yield fed, private, global_
    await fed.aclose()
    await global_.aclose()


async def test_forget_private_entry_hard_deletes(federation):
    fed, private, _ = federation
    entry = _entry(scope=MemoryScope.PRIVATE)
    await fed.write(entry)

    result = await fed.forget(entry.id)
    assert result.found is True
    assert result.scope is MemoryScope.PRIVATE
    assert result.hard is True
    assert await private.get(entry.id) is None


async def test_forget_unknown_id_reports_not_found(federation):
    fed, _, _ = federation
    result = await fed.forget(uuid4())
    assert result.found is False
    assert result.scope is None


async def test_forget_global_is_refused_and_entry_survives(federation):
    fed, _, global_ = federation
    entry = _entry(scope=MemoryScope.GLOBAL, importance=0.95)
    await global_.write(entry)

    with pytest.raises(GlobalDeleteRefused) as excinfo:
        await fed.forget(entry.id)
    assert excinfo.value.memory_id == entry.id
    # The GLOBAL store is untouched — the World owns it.
    assert await global_.get(entry.id) is not None


async def test_forget_archive_soft_deletes(federation):
    fed, private, _ = federation
    entry = _entry(scope=MemoryScope.PRIVATE)
    await fed.write(entry)

    result = await fed.forget(entry.id, hard=False)
    assert result.found is True and result.hard is False
    row = await private.get(entry.id)
    assert row is not None and row.archived is True


async def test_forget_read_only_tier_is_refused(tmp_path: Path):
    """A connector-backed pool is read-only; forget must refuse, not crash."""
    agent = uuid4()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "fact.md").write_text("a durable fact")

    private = SQLiteAdapter(tmp_path / "private.db")
    await private.connect()
    connector = MarkdownFolderAdapter(vault, agent, scope=MemoryScope.SHARED, pool_name="vault")
    router = MemoryRouter(private=private)
    router.register_pool("vault", connector)
    fed = MemoryFederation(router)

    note = (await connector.scan())[0]
    with pytest.raises(ReadOnlyTierDelete) as excinfo:
        await fed.forget(note.entry.id)
    assert excinfo.value.memory_id == note.entry.id
    await fed.aclose()


# --------------------------------------------------------------------------
# MemoryFederation.get
# --------------------------------------------------------------------------


async def test_federation_get_resolves_across_tiers(federation):
    fed, _, global_ = federation
    g = _entry(scope=MemoryScope.GLOBAL)
    await global_.write(g)
    p = _entry(scope=MemoryScope.PRIVATE)
    await fed.write(p)

    assert (await fed.get(p.id)).id == p.id
    assert (await fed.get(g.id)).id == g.id
    assert await fed.get(uuid4()) is None
