"""Unit tests for MemoryFederation: fan-out writes, merged reads. No LLM calls."""

from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import MemoryFederation, MemoryRouter, SQLiteAdapter
from arcana.types import (
    AdapterHealth,
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    MemoryWeights,
    PruneMode,
    PrunePolicy,
    PruneReport,
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

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id=self.name, healthy=not self.fail)


class _PrunableAdapter(_RecordingAdapter):
    """A recording adapter that also supports pruning, returning a fixed report."""

    def __init__(self, name: str, *, report: PruneReport) -> None:
        super().__init__(name)
        self.report = report
        self.prune_calls: list[PrunePolicy] = []

    async def prune(self, policy: PrunePolicy) -> PruneReport:
        self.prune_calls.append(policy)
        return self.report


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


# --------------------------------------------------------------------------
# stream_search
# --------------------------------------------------------------------------


async def test_stream_search_matches_search():
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

    query = MemoryQuery()
    streamed = [e.id async for e in fed.stream_search(query)]
    listed = [e.id for e in await fed.search(query)]
    assert streamed == listed  # same order, same dedup


async def test_stream_search_yields_best_first():
    weights = MemoryWeights(episodic=0.1, procedural=0.9)
    ep = _entry(type=MemoryType.EPISODIC, importance=0.6, content="ep")
    proc = _entry(type=MemoryType.PROCEDURAL, importance=0.6, content="proc")
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("private", seed=[ep, proc]), weights=weights))

    streamed = [e.content async for e in fed.stream_search(MemoryQuery())]
    assert streamed == ["proc", "ep"]


async def test_stream_search_honors_limit():
    a = _entry(importance=0.9, content="a")
    b = _entry(importance=0.5, content="b")
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("private", seed=[a, b])))

    streamed = [e.content async for e in fed.stream_search(MemoryQuery(limit=1))]
    assert streamed == ["a"]


async def test_stream_search_supports_early_break():
    high = _entry(importance=0.95, content="high")
    low = _entry(importance=0.1, content="low")
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("private", seed=[high, low])))

    first = None
    async for entry in fed.stream_search(MemoryQuery()):
        first = entry
        break  # consumer stops after the top-ranked entry
    assert first is not None
    assert first.content == "high"


async def test_stream_search_degrades_when_a_tier_fails(caplog):
    good = _entry(content="survivor")
    fed = MemoryFederation(
        MemoryRouter(
            private=_RecordingAdapter("private", seed=[good]),
            global_=_RecordingAdapter("global", fail=True),
        )
    )

    streamed = [e.content async for e in fed.stream_search(MemoryQuery())]
    assert streamed == ["survivor"]
    assert any("failed during search" in r.message for r in caplog.records)


async def test_stream_search_empty_routing_yields_nothing():
    fed = MemoryFederation(MemoryRouter(private=_RecordingAdapter("private")))
    streamed = [e async for e in fed.stream_search(MemoryQuery(scope=MemoryScope.GLOBAL))]
    assert streamed == []


async def test_stream_search_end_to_end(tmp_path: Path):
    private = SQLiteAdapter(tmp_path / "private.db")
    global_ = SQLiteAdapter(tmp_path / "global.db")
    await private.connect()
    await global_.connect()
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    agent = uuid4()
    entry = _entry(agent_id=agent, importance=0.95, scope=MemoryScope.PRIVATE, content="promote me")
    await fed.write(entry)

    streamed = [e.content async for e in fed.stream_search(MemoryQuery(agent_id=agent))]
    assert streamed == ["promote me"]  # present once despite living in two tiers

    await private.aclose()
    await global_.aclose()


# --------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------


async def test_prune_aggregates_across_tiers():
    private = _PrunableAdapter("private", report=PruneReport(scanned=10, archived=3))
    global_ = _PrunableAdapter("global", report=PruneReport(scanned=5, archived=1))
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    report = await fed.prune(PrunePolicy(min_importance=0.1))
    assert report.scanned == 15
    assert report.archived == 4
    assert report.tiers == 2
    assert private.prune_calls and global_.prune_calls  # every tier was pruned


async def test_prune_skips_tiers_without_prune_support():
    private = _PrunableAdapter("private", report=PruneReport(scanned=4, archived=2))
    plain_pool = _RecordingAdapter("pool")  # no prune method
    fed = MemoryFederation(MemoryRouter(private=private, pools={"team": plain_pool}))

    report = await fed.prune(PrunePolicy(min_importance=0.1))
    assert report.tiers == 1  # only the prunable private tier counted
    assert report.archived == 2


async def test_prune_propagates_tier_failure():
    class _BoomPrunable(_RecordingAdapter):
        async def prune(self, policy: PrunePolicy) -> PruneReport:
            raise RuntimeError("prune boom")

    fed = MemoryFederation(MemoryRouter(private=_BoomPrunable("private")))
    with pytest.raises(RuntimeError, match="prune boom"):
        await fed.prune(PrunePolicy(min_importance=0.1))


async def test_prune_end_to_end(tmp_path: Path):
    private = SQLiteAdapter(tmp_path / "private.db")
    global_ = SQLiteAdapter(tmp_path / "global.db")
    await private.connect()
    await global_.connect()
    fed = MemoryFederation(MemoryRouter(private=private, global_=global_))

    agent = uuid4()
    await fed.write(_entry(agent_id=agent, importance=0.05, scope=MemoryScope.PRIVATE, content="weak"))
    await fed.write(_entry(agent_id=agent, importance=0.8, scope=MemoryScope.PRIVATE, content="strong"))

    report = await fed.prune(PrunePolicy(min_importance=0.1, mode=PruneMode.PURGE))
    assert report.purged == 1

    survivors = [e.content for e in await fed.search(MemoryQuery(agent_id=agent))]
    assert survivors == ["strong"]

    await private.aclose()
    await global_.aclose()
