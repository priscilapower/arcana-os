"""Where the World sends its quality signals — the learning-loop sink seam.

:class:`QualitySignalSink` is the narrow interface the engine emits through; it
never inspects the sink or waits on a result. :class:`LearningSignalLog` is a
plain append-only JSONL sink (one signal per line) that mirrors the routing
audit's fail-open posture: a write that fails is logged and swallowed, so a
signal can never block or fail a route.
"""

import logging
from pathlib import Path
from typing import Protocol

from arcana.types import SessionQualitySignal

logger = logging.getLogger("arcana.world.learning")


class QualitySignalSink(Protocol):
    """Receives :class:`SessionQualitySignal`s, fire-and-forget.

    Implementations must not raise and must return promptly — the engine calls
    :meth:`emit` on the routing path and never awaits or checks a result.
    """

    def emit(self, signal: SessionQualitySignal) -> None: ...


class LearningSignalLog:
    """Append-only JSONL sink for quality signals. Each signal is one line."""

    DEFAULT_PATH: Path = Path.home() / ".arcana" / "world" / "quality_signals.jsonl"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("could not create learning signal dir %s", self._path.parent)

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, signal: SessionQualitySignal) -> None:
        """Serialize the signal to JSONL and append. Fail-open on any error."""
        try:
            line = signal.model_dump_json() + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:  # noqa: BLE001 — signal emission must never strand a route
            logger.warning("quality signal write failed for signal %s", signal.id, exc_info=True)

    def tail(self, n: int = 50) -> list[SessionQualitySignal]:
        """Return the last ``n`` signals, oldest-first. Empty if none/unreadable."""
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        collected: list[SessionQualitySignal] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                collected.append(SessionQualitySignal.model_validate_json(line))
            except ValueError:
                continue
            if len(collected) >= n:
                break
        return list(reversed(collected))
