"""Append-only JSONL audit for routing decisions.

Every :class:`RoutingDecision` is written here — one JSON line — **before** the
resolved agent executes, so a route is inspectable even if the run never starts.

The write is **fail-open**: the record is diagnostic, not a control gate, and the
route must never be stranded by a failed audit. A write (or even the directory
setup) that fails logs a warning and is swallowed, so :meth:`append` and the
constructor never raise.
"""

import logging
from pathlib import Path

from arcana.types import RoutingDecision

logger = logging.getLogger("arcana.world.audit")


class RoutingAuditLog:
    """Append-only JSONL log of routing decisions. Each decision is one line."""

    DEFAULT_PATH: Path = Path.home() / ".arcana" / "world" / "routing_audit.jsonl"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("could not create routing audit dir %s", self._path.parent)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, decision: RoutingDecision) -> None:
        """Serialize the decision to JSONL and append. Fail-open on any error."""
        try:
            line = decision.model_dump_json() + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:  # noqa: BLE001 — audit must never strand a route (fail-open)
            logger.warning("routing audit write failed for decision %s", decision.id, exc_info=True)

    def tail(self, n: int = 50) -> list[RoutingDecision]:
        """Return the last ``n`` decisions, oldest-first. Empty if none/unreadable."""
        if not self._path.exists():
            return []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        collected: list[RoutingDecision] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                collected.append(RoutingDecision.model_validate_json(line))
            except ValueError:
                continue
            if len(collected) >= n:
                break
        return list(reversed(collected))
