"""Resilience primitives for the memory federation.

A slow, locked, or corrupt store must not stall or fail a whole session. The
federation wraps each tier so failures are bounded (timeouts), contained (a bad
tier is skipped, not propagated), and surfaced (degraded events) rather than
silently swallowed or fatally raised.

This module currently holds the circuit breaker. The tier wrapper that composes
it with timeouts and degraded-event emission is added alongside it.
"""

import time
from collections.abc import Callable
from enum import StrEnum


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
