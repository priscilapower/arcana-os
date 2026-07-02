"""Unit tests for ResilientTier: timeouts, breaker integration, degraded events.

Reads must stay total (never raise); writes must raise TierWriteFailed carrying
the tier's scope. Corruption quarantines (forces the breaker open) rather than
counting as a single transient failure.
"""

import asyncio
from uuid import uuid4

import pytest

from arcana.memory import BreakerState, CircuitBreaker, ResilientTier, TierResilienceConfig, TierWriteFailed
from arcana.memory.errors import MemoryCorruptError
from arcana.observability.events import MemoryDegradedEvent
from arcana.types import (
    AdapterHealth,
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetrievalMode,
)

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


class _FakeInner:
    """Configurable MemoryAdapter stand-in that records how often it was called."""

    def __init__(
        self,
        *,
        search_delay: float = 0.0,
        search_exc: BaseException | None = None,
        seed: list[MemoryEntry] | None = None,
        write_delay: float = 0.0,
        write_exc: BaseException | None = None,
        healthy: bool = True,
    ) -> None:
        self.search_delay = search_delay
        self.search_exc = search_exc
        self.seed = seed or []
        self.write_delay = write_delay
        self.write_exc = write_exc
        self.healthy = healthy
        self.search_calls = 0
        self.write_calls = 0
        self.writes: list[MemoryEntry] = []

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        self.search_calls += 1
        if self.search_delay:
            await asyncio.sleep(self.search_delay)
        if self.search_exc is not None:
            raise self.search_exc
        return list(self.seed)

    async def write(self, entry: MemoryEntry) -> None:
        self.write_calls += 1
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        if self.write_exc is not None:
            raise self.write_exc
        self.writes.append(entry)

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id="fake", healthy=self.healthy)


def _entry() -> MemoryEntry:
    return MemoryEntry(agent_id=uuid4(), type=MemoryType.EPISODIC, content="x")


def _config(**overrides: object) -> TierResilienceConfig:
    base: dict[str, object] = dict(read_timeout_ms=20, semantic_timeout_ms=20, write_timeout_ms=20, fail_threshold=2)
    base.update(overrides)
    return TierResilienceConfig(**base)  # type: ignore[arg-type]


def _make(
    inner: _FakeInner,
    *,
    scope: MemoryScope = MemoryScope.PRIVATE,
    label: str = "private",
    config: TierResilienceConfig | None = None,
    breaker: CircuitBreaker | None = None,
) -> tuple[ResilientTier, list[MemoryDegradedEvent], CircuitBreaker]:
    events: list[MemoryDegradedEvent] = []
    br = breaker or CircuitBreaker(fail_threshold=2)
    tier = ResilientTier(
        inner,
        scope=scope,
        label=label,
        config=config or _config(),
        breaker=br,
        on_degraded=events.append,
    )
    return tier, events, br


_KEYWORD = MemoryQuery(text="hi", retrieval_mode=RetrievalMode.keyword)


# --------------------------------------------------------------------------
# Protocol identity
# --------------------------------------------------------------------------


def test_satisfies_memory_adapter_protocol():
    tier, _, _ = _make(_FakeInner())
    assert isinstance(tier, MemoryAdapter)


# --------------------------------------------------------------------------
# Reads — total, degrade on every failure mode
# --------------------------------------------------------------------------


async def test_happy_read_passes_through():
    inner = _FakeInner(seed=[_entry()])
    tier, events, _ = _make(inner)
    result = await tier.search(_KEYWORD)
    assert len(result) == 1
    assert events == []


async def test_timeout_read_returns_empty_and_degrades():
    inner = _FakeInner(search_delay=0.1)  # exceeds the 20ms budget
    tier, events, _ = _make(inner, config=_config(read_timeout_ms=10))
    result = await tier.search(_KEYWORD)
    assert result == []
    assert len(events) == 1
    assert events[0].reason == "timeout"
    assert events[0].operation == "read"


async def test_backend_error_read_returns_empty_and_degrades():
    inner = _FakeInner(search_exc=RuntimeError("boom"))
    tier, events, _ = _make(inner)
    result = await tier.search(_KEYWORD)
    assert result == []
    assert events[0].reason == "backend_error"


async def test_corruption_read_quarantines_the_tier():
    inner = _FakeInner(search_exc=MemoryCorruptError("malformed"))
    # a high threshold proves corruption opens the breaker on its own, not by count
    tier, events, breaker = _make(inner, breaker=CircuitBreaker(fail_threshold=99))
    result = await tier.search(_KEYWORD)
    assert result == []
    assert events[0].reason == "corruption"
    assert breaker.state is BreakerState.OPEN


async def test_open_breaker_skips_read_without_calling_inner():
    inner = _FakeInner(seed=[_entry()])
    breaker = CircuitBreaker(fail_threshold=1)
    breaker.force_open()
    tier, events, _ = _make(inner, breaker=breaker)
    result = await tier.search(_KEYWORD)
    assert result == []
    assert inner.search_calls == 0
    assert events[0].reason == "breaker_open"


async def test_repeated_failures_open_the_breaker_then_skip():
    inner = _FakeInner(search_exc=RuntimeError("boom"))
    tier, events, breaker = _make(inner, breaker=CircuitBreaker(fail_threshold=2))
    await tier.search(_KEYWORD)
    await tier.search(_KEYWORD)  # second failure trips the breaker
    assert breaker.state is BreakerState.OPEN
    calls_before = inner.search_calls
    await tier.search(_KEYWORD)  # now short-circuited
    assert inner.search_calls == calls_before
    assert events[-1].reason == "breaker_open"


async def test_semantic_and_keyword_budgets_differ():
    # keyword budget is tight (10ms), semantic budget is generous (500ms);
    # a 60ms backend times out on keyword but answers on semantic.
    cfg = _config(read_timeout_ms=10, semantic_timeout_ms=500)
    inner = _FakeInner(search_delay=0.06, seed=[_entry()])
    tier, events, _ = _make(inner, config=cfg)

    assert await tier.search(MemoryQuery(text="x", retrieval_mode=RetrievalMode.keyword)) == []
    assert events[-1].reason == "timeout"

    semantic = await tier.search(MemoryQuery(text="x", retrieval_mode=RetrievalMode.semantic))
    assert len(semantic) == 1


# --------------------------------------------------------------------------
# Writes — partial, raise TierWriteFailed carrying scope
# --------------------------------------------------------------------------


async def test_happy_write_passes_through():
    inner = _FakeInner()
    tier, events, _ = _make(inner)
    await tier.write(_entry())
    assert len(inner.writes) == 1
    assert events == []


async def test_write_timeout_raises_tier_write_failed():
    inner = _FakeInner(write_delay=0.1)
    tier, events, _ = _make(inner, scope=MemoryScope.SHARED, config=_config(write_timeout_ms=10))
    with pytest.raises(TierWriteFailed) as exc_info:
        await tier.write(_entry())
    assert exc_info.value.scope is MemoryScope.SHARED
    assert exc_info.value.reason == "timeout"
    # writes surface by raising; the federation owns the degraded event
    assert events == []


async def test_write_backend_error_raises_with_scope_and_cause():
    cause = RuntimeError("disk full")
    inner = _FakeInner(write_exc=cause)
    tier, events, _ = _make(inner, scope=MemoryScope.GLOBAL)
    with pytest.raises(TierWriteFailed) as exc_info:
        await tier.write(_entry())
    assert exc_info.value.scope is MemoryScope.GLOBAL
    assert exc_info.value.cause is cause
    assert exc_info.value.reason == "backend_error"
    assert events == []


async def test_write_corruption_quarantines():
    inner = _FakeInner(write_exc=MemoryCorruptError("malformed"))
    tier, _, breaker = _make(inner, breaker=CircuitBreaker(fail_threshold=99))
    with pytest.raises(TierWriteFailed) as exc_info:
        await tier.write(_entry())
    assert breaker.state is BreakerState.OPEN
    assert exc_info.value.reason == "corruption"


async def test_open_breaker_write_raises_without_calling_inner():
    inner = _FakeInner()
    breaker = CircuitBreaker(fail_threshold=1)
    breaker.force_open()
    tier, _, _ = _make(inner, breaker=breaker)
    with pytest.raises(TierWriteFailed) as exc_info:
        await tier.write(_entry())
    assert exc_info.value.scope is MemoryScope.PRIVATE
    assert inner.write_calls == 0
    assert exc_info.value.reason == "breaker_open"


# --------------------------------------------------------------------------
# Health probe
# --------------------------------------------------------------------------


async def test_health_check_delegates_to_inner():
    healthy = await _make(_FakeInner(healthy=True))[0].health_check()
    assert healthy.healthy
    unhealthy = await _make(_FakeInner(healthy=False))[0].health_check()
    assert not unhealthy.healthy
