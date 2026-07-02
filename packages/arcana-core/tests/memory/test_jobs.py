"""Unit tests for the background memory job queue.

Interface + load-shed behaviour. There is no consumer wired into the live path
yet — these test the queue's producer-side contract in isolation.
"""

import pytest

from arcana.memory import BackgroundJobQueue, MemoryJob, MemoryJobKind


async def _noop() -> None:
    return None


def _job(kind: MemoryJobKind = MemoryJobKind.EXTRACTION) -> MemoryJob:
    return MemoryJob(kind=kind, run=_noop, label=kind.value)


def test_submit_enqueues_and_tracks_depth():
    q = BackgroundJobQueue()
    assert q.depth() == 0
    assert q.submit(_job(), critical=False) is True
    assert q.depth() == 1


def test_pop_next_is_fifo_and_empties():
    q = BackgroundJobQueue()
    a, b = _job(MemoryJobKind.EXTRACTION), _job(MemoryJobKind.CONSOLIDATION)
    q.submit(a, critical=False)
    q.submit(b, critical=False)
    assert q.pop_next() is a
    assert q.pop_next() is b
    assert q.pop_next() is None
    assert q.depth() == 0


def test_drain_estimate_scales_with_depth_and_service_time():
    q = BackgroundJobQueue(initial_service_seconds=0.5)
    q.submit(_job(), critical=True)
    q.submit(_job(), critical=True)
    assert q.drain_estimate_seconds() == pytest.approx(1.0)  # 2 × 0.5s


def test_non_critical_job_is_shed_when_backlog_exceeds_headroom():
    # service 1s/job, headroom 2s → the queue accepts until the drain estimate
    # would exceed 2s, then sheds further non-critical work.
    q = BackgroundJobQueue(max_drain_seconds=2.0, initial_service_seconds=1.0)
    assert q.submit(_job(), critical=False) is True  # estimate 0 → accepted, depth 1
    assert q.submit(_job(), critical=False) is True  # estimate 1s → accepted, depth 2
    assert q.submit(_job(), critical=False) is False  # estimate 2s > headroom → shed
    assert q.depth() == 2


def test_critical_job_is_never_shed():
    q = BackgroundJobQueue(max_drain_seconds=0.0, initial_service_seconds=1.0)
    # headroom 0 → any non-critical is shed, but critical always lands.
    assert q.submit(_job(), critical=False) is False
    assert q.submit(_job(MemoryJobKind.PREFERENCE), critical=True) is True
    assert q.depth() == 1


def test_record_service_time_updates_the_estimate():
    q = BackgroundJobQueue(initial_service_seconds=1.0, ewma_alpha=0.5)
    q.submit(_job(), critical=True)
    q.record_service_time(3.0)  # ewma → 0.5*3 + 0.5*1 = 2.0
    assert q.drain_estimate_seconds() == pytest.approx(2.0)


def test_invalid_ewma_alpha_raises():
    with pytest.raises(ValueError):
        BackgroundJobQueue(ewma_alpha=0.0)
