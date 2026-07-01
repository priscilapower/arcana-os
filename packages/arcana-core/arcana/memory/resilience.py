"""Resilience primitives for the memory federation.

A slow, locked, or corrupt store must not stall or fail a whole session. The
federation wraps each tier so failures are bounded (timeouts), contained (a bad
tier is skipped, not propagated), and surfaced (degraded events) rather than
silently swallowed or fatally raised.

This module currently holds the circuit breaker. The tier wrapper that composes
it with timeouts and degraded-event emission is added alongside it.
"""

import asyncio
import time
from collections.abc import Callable
from enum import StrEnum

from arcana.memory.config import TierResilienceConfig
from arcana.memory.errors import MemoryCorruptError, TierWriteFailed
from arcana.observability import MemoryDegradedEvent, emit_degraded, get_metrics
from arcana.types import AdapterHealth, MemoryAdapter, MemoryEntry, MemoryQuery, MemoryScope, RetrievalMode


class BreakerState(StrEnum):
    """Lifecycle of a per-adapter circuit breaker."""

    CLOSED = "closed"  # healthy — calls pass through
    OPEN = "open"  # tripped — calls are skipped until the cooldown elapses
    HALF_OPEN = "half_open"  # cooldown elapsed — one probe call is allowed through


class CircuitBreaker:
    """Trips after consecutive failures; recovers via a single probe call.

    The breaker is *tripped* by observed failures during real traffic — a passing
    health check does not prove queries succeed, and we will not poll on every
    call. Once open it fails fast (``allow`` returns ``False``) until
    ``reset_after_seconds`` passes, after which one HALF_OPEN probe decides
    whether to close again or re-open.

    The clock is injected so callers (and tests) control time without sleeping;
    it must be a monotonic source of seconds.
    """

    def __init__(
        self,
        *,
        fail_threshold: int = 3,
        reset_after_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if fail_threshold < 1:
            raise ValueError("fail_threshold must be >= 1")
        self._fail_threshold = fail_threshold
        self._reset_after = reset_after_seconds
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        """Current state, accounting for an elapsed cooldown (OPEN → HALF_OPEN)."""
        if self._state is BreakerState.OPEN and self._cooldown_elapsed():
            self._state = BreakerState.HALF_OPEN
        return self._state

    def allow(self) -> bool:
        """Whether a call may proceed. ``False`` means skip (breaker open, cooling)."""
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        """A call succeeded: reset failures and close the breaker."""
        self._consecutive_failures = 0
        self._opened_at = None
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        """A call failed: count it and open the breaker at the threshold.

        A failure while HALF_OPEN re-opens immediately regardless of the count —
        the probe told us the backend is still unhealthy.
        """
        if self.state is BreakerState.HALF_OPEN:
            self._trip()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._fail_threshold:
            self._trip()

    def force_open(self) -> None:
        """Open the breaker immediately, ignoring the failure count.

        Used for conditions known to be session-long rather than transient —
        chiefly store corruption, where retrying within the session is pointless.
        """
        self._trip()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()

    def _cooldown_elapsed(self) -> bool:
        if self._opened_at is None:
            return False
        return (self._clock() - self._opened_at) >= self._reset_after


_STATE_CODE: dict[BreakerState, int] = {
    BreakerState.CLOSED: 0,
    BreakerState.HALF_OPEN: 1,
    BreakerState.OPEN: 2,
}


class _BreakerOpen(Exception):
    """Sentinel cause for a write rejected because the breaker was open."""


class ResilientTier:
    """Wraps one memory backend with a timeout, a circuit breaker, and degraded
    reporting. Drops into ``TierBackend.adapter`` in place of the raw adapter.

    Reads are *total* — a timeout, open breaker, corruption, or backend error
    yields ``[]`` (partial context beats none) after emitting a
    ``MemoryDegradedEvent``. Writes are *partial* — the same conditions raise
    ``TierWriteFailed`` carrying this tier's scope, so the federation can decide
    the blast radius (PRIVATE fatal; SHARED/GLOBAL degrade).

    Corruption is special: it is a session-long condition, so a
    ``MemoryCorruptError`` forces the breaker open (quarantine) rather than
    counting as one transient failure.
    """

    def __init__(
        self,
        inner: MemoryAdapter,
        *,
        scope: MemoryScope,
        label: str,
        config: TierResilienceConfig,
        breaker: CircuitBreaker,
        on_degraded: Callable[[MemoryDegradedEvent], None] | None = None,
    ) -> None:
        self._inner = inner
        self._scope = scope
        self._label = label
        self._config = config
        self._breaker = breaker
        self._on_degraded = on_degraded or emit_degraded

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Fan a query to the backend within budget; never raise."""
        if not self._breaker.allow():
            self._degrade("read", "breaker_open", "circuit open; tier skipped")
            self._publish_state()
            return []

        budget = self._read_budget(query)
        started = time.perf_counter()

        try:
            result = await asyncio.wait_for(self._inner.search(query), budget)
        except TimeoutError:
            self._breaker.record_failure()
            self._degrade("read", "timeout", f"read exceeded {budget:.3f}s")
            return []
        except MemoryCorruptError as exc:
            self._breaker.force_open()
            self._degrade("read", "corruption", str(exc))
            return []
        except Exception as exc:  # noqa: BLE001 — reads must stay total
            self._breaker.record_failure()
            self._degrade("read", "backend_error", str(exc))
            return []
        else:
            self._breaker.record_success()
            self._record_latency("read", started)
            return result
        finally:
            self._publish_state()

    async def write(self, entry: MemoryEntry) -> None:
        """Write to the backend within budget; raise ``TierWriteFailed`` on failure."""
        if not self._breaker.allow():
            self._degrade("write", "breaker_open", "circuit open; tier skipped")
            self._publish_state()
            raise TierWriteFailed(self._scope, _BreakerOpen("circuit open"))

        budget = self._config.write_timeout_ms / 1000
        started = time.perf_counter()

        try:
            await asyncio.wait_for(self._inner.write(entry), budget)
        except TimeoutError as exc:
            self._breaker.record_failure()
            self._degrade("write", "timeout", f"write exceeded {budget:.3f}s")
            raise TierWriteFailed(self._scope, exc) from exc
        except MemoryCorruptError as exc:
            self._breaker.force_open()
            self._degrade("write", "corruption", str(exc))
            raise TierWriteFailed(self._scope, exc) from exc
        except Exception as exc:
            self._breaker.record_failure()
            self._degrade("write", "backend_error", str(exc))
            raise TierWriteFailed(self._scope, exc) from exc
        else:
            self._breaker.record_success()
            self._record_latency("write", started)
        finally:
            self._publish_state()

    async def health_check(self) -> AdapterHealth:
        """Delegate to the wrapped backend — the breaker's half-open probe."""
        return await self._inner.health_check()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_budget(self, query: MemoryQuery) -> float:
        """Seconds budget for a read: the wider semantic budget unless keyword-only."""
        if query.retrieval_mode != RetrievalMode.keyword:
            return self._config.semantic_timeout_ms / 1000
        return self._config.read_timeout_ms / 1000

    def _degrade(self, operation: str, reason: str, message: str) -> None:
        event = MemoryDegradedEvent(
            agent_id="",
            session_id="",
            tier=self._label,
            operation=operation,  # type: ignore[arg-type]
            reason=reason,  # type: ignore[arg-type]
            message=message,
        )

        try:
            self._on_degraded(event)
        except Exception:  # noqa: BLE001 — observability must not break the path
            pass

    def _record_latency(self, operation: str, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            get_metrics().record_memory_tier_latency(tier=self._label, operation=operation, latency_ms=elapsed_ms)
        except Exception:  # noqa: BLE001
            pass

    def _publish_state(self) -> None:
        try:
            get_metrics().set_circuit_state(tier=self._label, state=_STATE_CODE[self._breaker.state])
        except Exception:  # noqa: BLE001
            pass
