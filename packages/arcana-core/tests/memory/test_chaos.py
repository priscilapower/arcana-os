"""End-to-end chaos drills for memory federation resilience.

Exercises the whole stack — MemoryFederation over a MemoryRouter that wraps each
tier in a ResilientTier — under adversarial backends: slow, failing, and corrupt.
Asserts guarantees hold end to end: reads stay total and return
survivors, corruption quarantines a tier without spreading, the breaker opens
then recovers, and a corrupt PRIVATE write is fatal while others degrade.

The per-component behaviour is unit-tested elsewhere (test_circuit_breaker,
test_resilient_tier, test_corruption); these tests are about the composition.
"""

import asyncio
import random
from uuid import uuid4

import pytest

from arcana.memory import (
    MemoryCorruptError,
    MemoryFederation,
    MemoryResilienceConfig,
    MemoryRouter,
    MemoryWriteError,
    TierResilienceConfig,
)
from arcana.observability.events import MemoryDegradedEvent
from arcana.types import AdapterHealth, MemoryEntry, MemoryQuery, MemoryScope, MemoryType, RetrievalMode

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


class _ChaosAdapter:
    """A backend whose behaviour is driven by a mutable ``mode``.

    ``ok`` returns the seed; ``slow`` sleeps past any sane budget; ``raise``
    throws a generic backend error; ``corrupt`` raises ``MemoryCorruptError``.
    ``calls`` counts how often the backend was actually reached — the measure of
    whether the breaker is short-circuiting it.
    """

    def __init__(self, name: str, *, mode: str = "ok", seed: list[MemoryEntry] | None = None, delay: float = 0.5):
        self.name = name
        self.mode = mode
        self.seed = seed or []
        self.delay = delay
        self.calls = 0
        self.writes: list[MemoryEntry] = []

    async def _misbehave(self) -> None:
        if self.mode == "slow":
            await asyncio.sleep(self.delay)
        elif self.mode == "raise":
            raise RuntimeError(f"{self.name} boom")
        elif self.mode == "corrupt":
            raise MemoryCorruptError(f"{self.name} malformed")

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        self.calls += 1
        await self._misbehave()
        return list(self.seed)

    async def write(self, entry: MemoryEntry) -> None:
        self.calls += 1
        await self._misbehave()
        self.writes.append(entry)

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id=self.name, healthy=self.mode == "ok")


def _entry(content: str, *, scope: MemoryScope = MemoryScope.PRIVATE, pool_name: str | None = None) -> MemoryEntry:
    return MemoryEntry(agent_id=uuid4(), type=MemoryType.EPISODIC, content=content, scope=scope, pool_name=pool_name)


def _fast_config(*, fail_threshold: int = 2, reset_after_seconds: float = 0.05) -> MemoryResilienceConfig:
    """A resilience config with tight budgets so slow tiers time out in tests."""
    tier = TierResilienceConfig(
        read_timeout_ms=30,
        semantic_timeout_ms=30,
        write_timeout_ms=30,
        fail_threshold=fail_threshold,
        reset_after_seconds=reset_after_seconds,
    )
    # Build via the "global" alias (a reserved word, so not a valid kwarg).
    return MemoryResilienceConfig.model_validate({"private": tier, "global": tier, "default_shared": tier})


_KEYWORD = MemoryQuery(text="x", retrieval_mode=RetrievalMode.keyword)


# --------------------------------------------------------------------------
# Read drills
# --------------------------------------------------------------------------


async def test_slow_tier_times_out_and_read_returns_survivors():
    fast = _ChaosAdapter("private", seed=[_entry("survivor")])
    slow = _ChaosAdapter("global", mode="slow", delay=1.0)  # far beyond the 30ms budget
    fed = MemoryFederation(MemoryRouter(private=fast, global_=slow, resilience=_fast_config()))

    got = await fed.search(_KEYWORD)

    assert [e.content for e in got] == ["survivor"]  # slow tier cancelled, session proceeds


async def test_corrupt_tier_is_quarantined_and_contained():
    good = _ChaosAdapter("private", seed=[_entry("survivor")])
    corrupt = _ChaosAdapter("global", mode="corrupt")
    fed = MemoryFederation(MemoryRouter(private=good, global_=corrupt, resilience=_fast_config()))

    first = await fed.search(_KEYWORD)
    second = await fed.search(_KEYWORD)

    # The good tier keeps answering; the corrupt tier is quarantined after one
    # hit (breaker forced open) and never reached again — blast radius contained.
    assert [e.content for e in first] == ["survivor"]
    assert [e.content for e in second] == ["survivor"]
    assert corrupt.calls == 1


async def test_breaker_opens_after_failures_then_recovers():
    flaky = _ChaosAdapter("private", mode="raise", seed=[_entry("ok")])
    cfg = _fast_config(fail_threshold=2, reset_after_seconds=0.05)
    fed = MemoryFederation(MemoryRouter(private=flaky, resilience=cfg))

    await fed.search(_KEYWORD)
    await fed.search(_KEYWORD)  # second failure trips the breaker
    assert flaky.calls == 2

    await fed.search(_KEYWORD)  # breaker open — backend short-circuited
    assert flaky.calls == 2

    flaky.mode = "ok"
    await asyncio.sleep(0.06)  # outlast the cooldown → half-open probe
    recovered = await fed.search(_KEYWORD)

    assert flaky.calls == 3  # one probe let through
    assert [e.content for e in recovered] == ["ok"]  # probe succeeded, breaker closed


# --------------------------------------------------------------------------
# Write drills
# --------------------------------------------------------------------------


async def test_corrupt_private_write_is_fatal():
    corrupt = _ChaosAdapter("private", mode="corrupt")
    fed = MemoryFederation(MemoryRouter(private=corrupt, resilience=_fast_config()))

    with pytest.raises(MemoryWriteError):
        await fed.write(_entry("x", scope=MemoryScope.PRIVATE))


async def test_corrupt_shared_write_degrades_not_fatal():
    corrupt_pool = _ChaosAdapter("kitchen", mode="corrupt")
    events: list[MemoryDegradedEvent] = []
    fed = MemoryFederation(
        MemoryRouter(private=_ChaosAdapter("private"), pools={"kitchen": corrupt_pool}, resilience=_fast_config()),
        on_degraded=events.append,
    )

    # A corrupt SHARED pool must not fail the session — it degrades.
    await fed.write(_entry("x", scope=MemoryScope.SHARED, pool_name="kitchen"))
    assert len(events) == 1
    assert events[0].operation == "write"
    assert events[0].reason == "corruption"
    assert events[0].tier == "shared:kitchen"


# --------------------------------------------------------------------------
# Totality property: search never raises under any mix of failing tiers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(24))
async def test_search_is_total_under_random_tier_failures(seed: int):
    rng = random.Random(seed)
    modes = ["ok", "raise", "corrupt", "slow"]

    specs = {
        "private": (MemoryScope.PRIVATE, None),
        "global": (MemoryScope.GLOBAL, None),
        "pool_a": (MemoryScope.SHARED, "a"),
        "pool_b": (MemoryScope.SHARED, "b"),
    }
    adapters: dict[str, _ChaosAdapter] = {}
    ok_contents: set[str] = set()
    for name, (scope, pool) in specs.items():
        mode = rng.choice(modes)
        adapters[name] = _ChaosAdapter(name, mode=mode, seed=[_entry(name, scope=scope, pool_name=pool)])
        if mode == "ok":
            ok_contents.add(name)

    router = MemoryRouter(
        private=adapters["private"],
        global_=adapters["global"],
        pools={"a": adapters["pool_a"], "b": adapters["pool_b"]},
        resilience=_fast_config(),
    )
    fed = MemoryFederation(router)

    got = await fed.search(_KEYWORD)  # must never raise, whatever the mix

    # Exactly the healthy tiers contribute; failing/slow ones drop out silently.
    assert {e.content for e in got} == ok_contents
