"""
AuditLog — append-only JSONL audit log for all Arcana observability events.

Phase 1: writes to ~/.arcana/logs/audit.jsonl (one record per line).
Phase 3: replaced by OTLP exporter; this file stays as the local fallback.

Design constraints:
  - No external dependencies (stdlib only after dataclasses.asdict)
  - Append-only: never modifies existing lines
  - Thread-safe for single-process use (Phase 1 is single-process)
  - Query methods power `arcana world audit` CLI commands
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


ARCANA_HOME = Path.home() / ".arcana"
DEFAULT_AUDIT_PATH = ARCANA_HOME / "logs" / "audit.jsonl"


class AuditLog:
    """
    Append-only structured event log.

    Usage:
        audit = AuditLog()
        audit.write(RoutingEvent(session_id=..., outcome="matched", ...))

        # Query
        recent_routing = audit.query_routing(limit=50)
        budget_events  = audit.query(event_type="ContextBudgetEvent", limit=20)
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._path = log_path or DEFAULT_AUDIT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, event: object) -> None:
        """
        Serialise an event dataclass and append it as a JSONL record.
        The _event_type field is injected automatically.
        """
        record = asdict(event)  # type: ignore[arg-type]
        record["_event_type"] = type(event).__name__
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    # ------------------------------------------------------------------
    # Generic query
    # ------------------------------------------------------------------

    def query(
        self,
        event_type: str | None = None,
        since: datetime | None = None,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Return the last `limit` records, optionally filtered by event type,
        time range, and workspace.
        """
        records = self._read_all()

        if event_type:
            records = [r for r in records if r.get("_event_type") == event_type]
        if since:
            cutoff = since.isoformat()
            records = [r for r in records if r.get("timestamp", "") >= cutoff]
        if workspace_id:
            records = [r for r in records if r.get("workspace_id") == workspace_id]

        return records[-limit:]

    def tail(self, n: int = 50, event_type: str | None = None) -> list[dict]:
        """Read the last n records; optionally filter by event type."""
        return self.query(event_type=event_type, limit=n)

    # ------------------------------------------------------------------
    # Typed queries — power the CLI audit commands
    # ------------------------------------------------------------------

    def query_routing(
        self,
        agent_id: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Query routing decisions. Powers `arcana world audit --routing`.

        Args:
            agent_id: filter to routes targeting a specific agent UUID
            outcome:  "matched" | "fallback" | "rejected" | "error"
            since:    only return events after this datetime
            limit:    max records to return (most recent first)
        """
        records = self.query(event_type="RoutingEvent", since=since, limit=limit * 10)

        if agent_id:
            records = [r for r in records if r.get("target_agent_id") == agent_id]
        if outcome:
            records = [r for r in records if r.get("outcome") == outcome]

        return records[-limit:]

    def routing_summary(self, since: datetime | None = None) -> dict:
        """
        Aggregated routing health metrics.
        Returns: total, matched, fallback, rejected, error counts + fallback_rate.
        """
        records = self.query_routing(since=since, limit=10_000)
        total    = len(records)
        matched  = sum(1 for r in records if r.get("outcome") == "matched")
        fallback = sum(1 for r in records if r.get("outcome") == "fallback")
        rejected = sum(1 for r in records if r.get("outcome") == "rejected")
        error    = sum(1 for r in records if r.get("outcome") == "error")
        return {
            "total":         total,
            "matched":       matched,
            "fallback":      fallback,
            "rejected":      rejected,
            "error":         error,
            "fallback_rate": round(fallback / total, 3) if total else 0.0,
        }

    def query_budget(
        self,
        agent_id: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        truncated_only: bool = False,
    ) -> list[dict]:
        """
        Query ContextBudget events. Powers `arcana world audit --budget`.

        Args:
            truncated_only: if True, only return sessions where a tier was cut
        """
        records = self.query(event_type="ContextBudgetEvent", since=since, limit=limit * 10)

        if agent_id:
            records = [r for r in records if r.get("agent_id") == agent_id]
        if truncated_only:
            records = [r for r in records if r.get("tiers_truncated")]

        return records[-limit:]

    def query_memory(
        self,
        agent_id: str | None = None,
        event_type: str = "MemoryReadEvent",
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Query memory read or write events. Powers `arcana world audit --memory`.

        Args:
            event_type: "MemoryReadEvent" or "MemoryWriteEvent"
        """
        records = self.query(event_type=event_type, since=since, limit=limit * 5)
        if agent_id:
            records = [r for r in records if r.get("agent_id") == agent_id]
        return records[-limit:]

    def session_events(self, session_id: str) -> list[dict]:
        """Return all events for a single session, in order. Powers `arcana logs session <id>`."""
        return [r for r in self._read_all() if r.get("session_id") == session_id]

    def token_totals(self, since: datetime | None = None) -> dict:
        """
        Aggregate token consumption from ModelCallEvents.
        Powers `arcana metrics`.
        """
        records = self.query(event_type="ModelCallEvent", since=since, limit=100_000)
        return {
            "sessions":      len({r.get("session_id") for r in records}),
            "input_tokens":  sum(r.get("input_tokens", 0) for r in records),
            "output_tokens": sum(r.get("output_tokens", 0) for r in records),
            "total_cost_usd": round(
                sum(r.get("cost_usd") or 0.0 for r in records), 4
            ),
            "model_calls":   len(records),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        result = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # skip malformed lines silently
        return result
