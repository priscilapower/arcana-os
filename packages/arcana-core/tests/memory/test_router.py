"""Unit tests for MemoryRouter. Pure routing decisions — no I/O, no LLM calls."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from arcana.memory import GLOBAL_PROMOTION_THRESHOLD, MemoryRouter, MemoryRoutingError
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


class _FakeAdapter:
    """Minimal MemoryAdapter stand-in. The router never calls these methods —
    it only routes to the instance — so they exist purely for protocol identity.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:  # pragma: no cover
        return []

    async def write(self, entry: MemoryEntry) -> None:  # pragma: no cover
        return None


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


def _router(*, with_global: bool = True, pools: list[str] | None = None) -> MemoryRouter:
    return MemoryRouter(
        private=_FakeAdapter("private"),
        global_=_FakeAdapter("global") if with_global else None,
        pools={n: _FakeAdapter(n) for n in (pools or [])},
    )


def test_runtime_protocol_conformance():
    # The fakes must satisfy the protocol the router is typed against.
    assert isinstance(_FakeAdapter("x"), MemoryAdapter)


# --------------------------------------------------------------------------
# route_write
# --------------------------------------------------------------------------


def test_private_low_importance_routes_private_only():
    router = _router()
    targets = router.route_write(_entry(importance=0.5, scope=MemoryScope.PRIVATE))
    assert [t.scope for t in targets] == [MemoryScope.PRIVATE]


def test_private_high_importance_also_routes_global():
    router = _router()
    targets = router.route_write(_entry(importance=0.95, scope=MemoryScope.PRIVATE))
    assert [t.scope for t in targets] == [MemoryScope.PRIVATE, MemoryScope.GLOBAL]


def test_promotion_threshold_boundary_is_inclusive():
    # The constant must match the entry's own promotion rule (>= 0.9).
    router = _router()
    targets = router.route_write(_entry(importance=GLOBAL_PROMOTION_THRESHOLD, scope=MemoryScope.PRIVATE))
    assert [t.scope for t in targets] == [MemoryScope.PRIVATE, MemoryScope.GLOBAL]


def test_private_high_importance_without_global_stays_private():
    router = _router(with_global=False)
    targets = router.route_write(_entry(importance=0.99, scope=MemoryScope.PRIVATE))
    assert [t.scope for t in targets] == [MemoryScope.PRIVATE]


def test_shared_routes_to_named_pool():
    router = _router(pools=["kitchen"])
    targets = router.route_write(_entry(scope=MemoryScope.SHARED, pool_name="kitchen"))
    assert len(targets) == 1
    assert targets[0].scope == MemoryScope.SHARED
    assert targets[0].pool_name == "kitchen"


def test_shared_without_pool_name_raises():
    router = _router(pools=["kitchen"])
    with pytest.raises(MemoryRoutingError):
        router.route_write(_entry(scope=MemoryScope.SHARED, pool_name=None))


def test_shared_unknown_pool_raises():
    router = _router(pools=["kitchen"])
    with pytest.raises(MemoryRoutingError):
        router.route_write(_entry(scope=MemoryScope.SHARED, pool_name="garage"))


def test_global_routes_to_global():
    router = _router()
    targets = router.route_write(_entry(scope=MemoryScope.GLOBAL))
    assert [t.scope for t in targets] == [MemoryScope.GLOBAL]


def test_global_without_backend_raises():
    router = _router(with_global=False)
    with pytest.raises(MemoryRoutingError):
        router.route_write(_entry(scope=MemoryScope.GLOBAL))


# --------------------------------------------------------------------------
# route_read
# --------------------------------------------------------------------------


def test_read_none_scope_fans_across_every_tier():
    router = _router(pools=["a", "b"])
    tiers = router.route_read(MemoryQuery())
    assert {t.scope for t in tiers} == {MemoryScope.PRIVATE, MemoryScope.SHARED, MemoryScope.GLOBAL}
    assert {t.pool_name for t in tiers if t.scope == MemoryScope.SHARED} == {"a", "b"}


def test_read_none_scope_skips_missing_global():
    router = _router(with_global=False, pools=["a"])
    tiers = router.route_read(MemoryQuery())
    assert MemoryScope.GLOBAL not in {t.scope for t in tiers}


def test_read_private_single_tier():
    router = _router(pools=["a"])
    tiers = router.route_read(MemoryQuery(scope=MemoryScope.PRIVATE))
    assert [t.scope for t in tiers] == [MemoryScope.PRIVATE]


def test_read_global_single_tier():
    router = _router()
    tiers = router.route_read(MemoryQuery(scope=MemoryScope.GLOBAL))
    assert [t.scope for t in tiers] == [MemoryScope.GLOBAL]


def test_read_global_missing_degrades_to_empty():
    router = _router(with_global=False)
    assert router.route_read(MemoryQuery(scope=MemoryScope.GLOBAL)) == []


def test_read_shared_named_pool():
    router = _router(pools=["a", "b"])
    tiers = router.route_read(MemoryQuery(scope=MemoryScope.SHARED, pool_name="b"))
    assert [t.pool_name for t in tiers] == ["b"]


def test_read_shared_all_pools_when_unnamed():
    router = _router(pools=["a", "b"])
    tiers = router.route_read(MemoryQuery(scope=MemoryScope.SHARED))
    assert {t.pool_name for t in tiers} == {"a", "b"}


def test_read_shared_unknown_named_pool_raises():
    router = _router(pools=["a"])
    with pytest.raises(MemoryRoutingError):
        router.route_read(MemoryQuery(scope=MemoryScope.SHARED, pool_name="z"))


def test_register_pool_makes_it_routable():
    router = _router()
    router.register_pool("late", _FakeAdapter("late"))
    targets = router.route_write(_entry(scope=MemoryScope.SHARED, pool_name="late"))
    assert targets[0].pool_name == "late"


# --------------------------------------------------------------------------
# rank
# --------------------------------------------------------------------------


def test_rank_weights_favor_a_memory_type():
    weights = MemoryWeights(episodic=0.1, procedural=0.9)
    router = MemoryRouter(private=_FakeAdapter("p"), weights=weights)
    episodic = _entry(type=MemoryType.EPISODIC, importance=0.6, content="ep")
    procedural = _entry(type=MemoryType.PROCEDURAL, importance=0.6, content="proc")
    ranked = router.rank([episodic, procedural], MemoryQuery())
    assert [e.content for e in ranked] == ["proc", "ep"]


def test_rank_pinned_sorts_first_regardless_of_score():
    router = MemoryRouter(private=_FakeAdapter("p"))
    high = _entry(importance=0.95, content="high")
    pinned_low = _entry(importance=0.05, content="pinned", pinned=True)
    ranked = router.rank([high, pinned_low], MemoryQuery())
    assert ranked[0].content == "pinned"


def test_rank_respects_limit():
    router = MemoryRouter(private=_FakeAdapter("p"))
    entries = [_entry(importance=i / 10, content=f"e{i}") for i in range(5)]
    ranked = router.rank(entries, MemoryQuery(limit=2))
    assert len(ranked) == 2


def test_rank_neutral_weights_order_by_importance_then_recency():
    router = MemoryRouter(private=_FakeAdapter("p"))  # neutral 0.5 weights
    now = datetime.now(UTC)
    older = _entry(importance=0.5, content="older", last_accessed_at=now - timedelta(days=1))
    newer = _entry(importance=0.5, content="newer", last_accessed_at=now)
    high = _entry(importance=0.9, content="high")
    ranked = router.rank([older, newer, high], MemoryQuery())
    assert [e.content for e in ranked] == ["high", "newer", "older"]
