"""Backpressure for background memory jobs.

Post-session extraction and periodic consolidation are meant to run *off* the
request path. Modelled as a bounded queue so a backlog degrades gracefully:
sessions never block on it, and when the queue's drain estimate exceeds its
headroom, non-critical jobs are load-shed rather than allowed to accumulate
unboundedly (the "Mathematics of Backlogs" discipline — model the queue before
it overflows).

**Design-ahead, not yet wired.** Extraction is still an inline synchronous write
in ``Agent``; there is no consumer draining this queue yet. Only the queue
interface and its depth/drain metrics ship here. The consumer loop and the
wiring into the agent land when consolidation actually moves off the request
path — until then nothing constructs this queue in the live path.
"""

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from arcana.observability import get_metrics


class MemoryJobKind(StrEnum):
    """What a deferred memory job does — and, by nature, how sheddable it is."""

    EXTRACTION = "extraction"  # distil a session exchange into memory — non-critical
    CONSOLIDATION = "consolidation"  # merge / promote / decay maintenance — non-critical
    PREFERENCE = "preference"  # persist a user-confirmed preference — critical


@dataclass
class MemoryJob:
    """A unit of deferred memory work.

    ``run`` is the coroutine factory a future consumer awaits; keeping the work
    behind a thunk lets the queue stay agnostic about what the job does.
    """

    kind: MemoryJobKind
    run: Callable[[], Awaitable[None]]
    label: str = ""


class BackgroundJobQueue:
    """A bounded FIFO queue for background memory work with load-shedding.

    ``submit`` never blocks: critical jobs are always enqueued, non-critical jobs
    are dropped (``submit`` returns ``False``) once the estimated drain time
    reaches ``max_drain_seconds`` (so a headroom of ``0`` sheds all non-critical
    work). The drain estimate is ``depth × EWMA(service
    time)``; the EWMA is seeded and refined by a consumer via
    ``record_service_time`` once one exists.
    """

    def __init__(
        self,
        *,
        max_drain_seconds: float = 30.0,
        initial_service_seconds: float = 0.1,
        ewma_alpha: float = 0.3,
    ) -> None:
        if not 0.0 < ewma_alpha <= 1.0:
            raise ValueError("ewma_alpha must be in (0, 1]")
        self._queue: deque[MemoryJob] = deque()
        self._max_drain = max_drain_seconds
        self._service_ewma = initial_service_seconds
        self._alpha = ewma_alpha
        self._publish()

    def submit(self, job: MemoryJob, *, critical: bool) -> bool:
        """Enqueue a job. Returns ``False`` if a non-critical job was load-shed.

        Critical work (e.g. a user-confirmed preference) is always accepted;
        non-critical work is shed when the backlog would take longer to drain
        than the configured headroom allows.
        """
        if not critical and self.drain_estimate_seconds() >= self._max_drain:
            return False
        self._queue.append(job)
        self._publish()
        return True

    def pop_next(self) -> MemoryJob | None:
        """Remove and return the oldest job, or ``None`` if the queue is empty.

        The single primitive a future consumer loop needs; updates the metrics.
        """
        if not self._queue:
            return None
        job = self._queue.popleft()
        self._publish()
        return job

    def depth(self) -> int:
        return len(self._queue)

    def drain_estimate_seconds(self) -> float:
        return len(self._queue) * self._service_ewma

    def record_service_time(self, seconds: float) -> None:
        """Fold an observed job service time into the EWMA used for drain estimates."""
        self._service_ewma = self._alpha * seconds + (1 - self._alpha) * self._service_ewma

    def _publish(self) -> None:
        try:
            metrics = get_metrics()
            metrics.set_queue_depth(self.depth())
            metrics.set_queue_drain_seconds(self.drain_estimate_seconds())
        except Exception:  # noqa: BLE001 — metrics must never break the memory path
            pass
