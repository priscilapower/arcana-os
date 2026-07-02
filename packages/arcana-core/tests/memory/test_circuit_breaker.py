"""Unit tests for CircuitBreaker. Pure state machine — time is injected, no sleeps."""

import pytest

from arcana.memory.resilience import BreakerState, CircuitBreaker


class _Clock:
    """A hand-cranked monotonic clock so cooldowns are tested without sleeping."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_starts_closed_and_allows():
    cb = CircuitBreaker()
    assert cb.state is BreakerState.CLOSED
    assert cb.allow()


def test_stays_closed_below_threshold():
    cb = CircuitBreaker(fail_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state is BreakerState.CLOSED
    assert cb.allow()


def test_opens_at_threshold():
    cb = CircuitBreaker(fail_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.state is BreakerState.OPEN
    assert not cb.allow()


def test_success_resets_failure_count():
    cb = CircuitBreaker(fail_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # counter back to zero
    cb.record_failure()
    cb.record_failure()
    # only two failures since the reset — still below threshold
    assert cb.state is BreakerState.CLOSED


def test_open_blocks_until_cooldown():
    clock = _Clock()
    cb = CircuitBreaker(fail_threshold=1, reset_after_seconds=30.0, clock=clock)
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    clock.t = 29.999
    assert not cb.allow()
    assert cb.state is BreakerState.OPEN


def test_half_open_after_cooldown():
    clock = _Clock()
    cb = CircuitBreaker(fail_threshold=1, reset_after_seconds=30.0, clock=clock)
    cb.record_failure()
    clock.t = 30.0
    assert cb.state is BreakerState.HALF_OPEN
    assert cb.allow()  # one probe permitted


def test_half_open_success_closes():
    clock = _Clock()
    cb = CircuitBreaker(fail_threshold=1, reset_after_seconds=10.0, clock=clock)
    cb.record_failure()
    clock.t = 10.0
    assert cb.state is BreakerState.HALF_OPEN
    cb.record_success()
    assert cb.state is BreakerState.CLOSED
    assert cb.allow()


def test_half_open_failure_reopens_immediately():
    clock = _Clock()
    cb = CircuitBreaker(fail_threshold=5, reset_after_seconds=10.0, clock=clock)
    cb.record_failure()  # 1 of 5 — not yet open
    assert cb.state is BreakerState.CLOSED
    # force open by exhausting the threshold, then cool down to half-open
    for _ in range(4):
        cb.record_failure()
    assert cb.state is BreakerState.OPEN
    clock.t = 10.0
    assert cb.state is BreakerState.HALF_OPEN
    cb.record_failure()  # a probe failure re-opens regardless of the counter
    assert cb.state is BreakerState.OPEN


def test_force_open():
    cb = CircuitBreaker(fail_threshold=100)
    cb.force_open()
    assert cb.state is BreakerState.OPEN
    assert not cb.allow()


def test_reopen_starts_a_fresh_cooldown():
    clock = _Clock()
    cb = CircuitBreaker(fail_threshold=1, reset_after_seconds=10.0, clock=clock)
    cb.record_failure()  # opened at t=0
    clock.t = 10.0
    assert cb.state is BreakerState.HALF_OPEN
    cb.record_failure()  # re-opened at t=10
    clock.t = 15.0
    assert cb.state is BreakerState.OPEN  # only 5s since reopen
    clock.t = 20.0
    assert cb.state is BreakerState.HALF_OPEN


def test_invalid_threshold_raises():
    with pytest.raises(ValueError):
        CircuitBreaker(fail_threshold=0)
