"""Unit tests for MemoryFederation: fan-out writes, merged reads. No LLM calls."""

from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import MemoryFederation, MemoryRouter, SQLiteAdapter
from arcana.types import (
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    MemoryWeights,
)

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


class _RecordingAdapter:
    """MemoryAdapter stand-in that records writes and returns seeded entries."""

    def __init__(self, name: str, *, seed: list[MemoryEntry] | None = None, fail: bool = False) -> None:
        self.name = name
        self.writes: list[MemoryEntry] = []
        self.seed = seed or []
        self.fail = fail

    async def write(self, entry: MemoryEntry) -> None:
        if self.fail:
            raise RuntimeError(f"{self.name} write boom")
        self.writes.append(entry)

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        if self.fail:
            raise RuntimeError(f"{self.name} search boom")
        return list(self.seed)


def _entry(**overrides) -> MemoryEntry:
    base = dict(
        agent_id=uuid4(),
        type=MemoryType.SEMANTIC,
        content="the sky is blue",
        importance=0.5,
        scope=MemoryScope.PRIVATE,
    )
    base.update(overrides)
    return MemoryEntry(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------


def test_federation_is_a_memory_adapter():
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("p")))
    assert isinstance(fed, MemoryAdapter)


# --------------------------------------------------------------------------
# Fan-out writes
# --------------------------------------------------------------------------


async def test_private_low_importance_writes_private_only():
    private = _RecordingAdapter("private")
    global_ = _RecordingAdapter("global")
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    entry = _entry(importance=0.5, scope=MemoryScope.PRIVATE)
    await fed.write(entry)

    assert [e.id for e in private.writes] == [entry.id]
    assert global_.writes == []


async def test_high_importance_private_fans_out_to_global_with_scope_rewrite():
    private = _RecordingAdapter("private")
    global_ = _RecordingAdapter("global")
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    entry = _entry(importance=0.95, scope=MemoryScope.PRIVATE)
    await fed.write(entry)

    assert [e.id for e in private.writes] == [entry.id]
    assert private.writes[0].scope == MemoryScope.PRIVATE

    assert len(global_.writes) == 1
    promoted = global_.writes[0]
    assert promoted.id == entry.id  # same id keeps it idempotent
    assert promoted.scope == MemoryScope.GLOBAL
    assert promoted.pool_name is None


async def test_shared_write_targets_named_pool_only():
    private = _RecordingAdapter("private")
    kitchen = _RecordingAdapter("kitchen")
    fed = MemoryFederation(MemoryRouter(private=private, pools={"kitchen": kitchen}))

    entry = _entry(scope=MemoryScope.SHARED, pool_name="kitchen")
    await fed.write(entry)

    assert [e.id for e in kitchen.writes] == [entry.id]
    assert kitchen.writes[0].scope == MemoryScope.SHARED
    assert private.writes == []


async def test_write_propagates_tier_failure():
    private = _RecordingAdapter("private")
    global_ = _RecordingAdapter("global", fail=True)
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    with pytest.raises(RuntimeError, match="global write boom"):
        await fed.write(_entry(importance=0.95, scope=MemoryScope.PRIVATE))


# --------------------------------------------------------------------------
# Merged reads
# --------------------------------------------------------------------------


async def test_read_merges_across_tiers():
    p = _entry(content="private fact")
    s = _entry(content="shared fact", scope=MemoryScope.SHARED, pool_name="team")
    g = _entry(content="global fact", scope=MemoryScope.GLOBAL)
    fed = MemoryFederation(
        MemoryRouter(
            private=_RecordingAdapter("private", seed=[p]),
            global_=_RecordingAdapter("global", seed=[g]),
            pools={"team": _RecordingAdapter("team", seed=[s])},
        )
    )

    got = await fed.search(MemoryQuery())
    assert {e.content for e in got} == {"private fact", "shared fact", "global fact"}


async def test_read_dedups_by_id_keeping_most_local_copy():
    # Same id surfaces from both private and global (a promoted entry). The
    # private copy (earlier in routing order) must win.
    shared_id = uuid4()
    private_copy = _entry(id=shared_id, content="local", scope=MemoryScope.PRIVATE)
    global_copy = _entry(id=shared_id, content="local", scope=MemoryScope.GLOBAL)
    fed = MemoryFederation(
        MemoryRouter(
            private=_RecordingAdapter("private", seed=[private_copy]),
            global_=_RecordingAdapter("global", seed=[global_copy]),
        )
    )

    got = await fed.search(MemoryQuery())
    assert len(got) == 1
    assert got[0].scope == MemoryScope.PRIVATE


async def test_read_applies_card_weight_ranking_and_limit():
    weights = MemoryWeights(episodic=0.1, procedural=0.9)
    ep = _entry(type=MemoryType.EPISODIC, importance=0.6, content="ep")
    proc = _entry(type=MemoryType.PROCEDURAL, importance=0.6, content="proc")
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("private", seed=[ep, proc]), weights=weights))

    ranked = await fed.search(MemoryQuery())
    assert [e.content for e in ranked] == ["proc", "ep"]

    capped = await fed.search(MemoryQuery(limit=1))
    assert [e.content for e in capped] == ["proc"]


async def test_read_degrades_when_a_tier_fails(caplog):
    good = _entry(content="survivor")
    fed = MemoryFederation(
        MemoryRouter(
            private=_RecordingAdapter("private", seed=[good]),
            global_=_RecordingAdapter("global", fail=True),
        )
    )

    got = await fed.search(MemoryQuery())
    assert [e.content for e in got] == ["survivor"]
    assert any("failed during search" in r.message for r in caplog.records)


async def test_read_with_empty_routing_returns_empty():
    # GLOBAL scope with no global backend → router yields no tiers.
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("private")))
    assert await fed.search(MemoryQuery(scope=MemoryScope.GLOBAL)) == []


# --------------------------------------------------------------------------
# End-to-end with real SQLite backends
# --------------------------------------------------------------------------


async def test_end_to_end_promotion_and_merge(tmp_path: Path):
    private = SQLiteAdapter(tmp_path / "private.db")
    global_ = SQLiteAdapter(tmp_path / "global.db")
    await private.connect()
    await global_.connect()
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    agent = uuid4()
    entry = _entry(agent_id=agent, importance=0.95, scope=MemoryScope.PRIVATE, content="promote me")
    await fed.write(entry)

    # Federated read: present once despite living in two tiers.
    merged = await fed.search(MemoryQuery(agent_id=agent))
    assert [e.content for e in merged] == ["promote me"]

    # The promoted copy is genuinely in the global store.
    in_global = await global_.search(MemoryQuery(scope=MemoryScope.GLOBAL))
    assert len(in_global) == 1
    assert in_global[0].id == entry.id

    await private.aclose()
    await global_.aclose()
