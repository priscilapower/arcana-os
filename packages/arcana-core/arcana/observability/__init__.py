"""Arcana observability — audit log, OTel tracing, and metrics.

Quick start::

    from arcana.observability import configure_observability, get_audit_log

    configure_observability()       # writes to ~/.arcana/logs/ by default
    log = get_audit_log()
    events = log.tail(n=20)

Install OTel extras for span export::

    pip install arcana-core[observability]
"""

from pathlib import Path

from arcana.observability.audit import AuditLog
from arcana.observability.events import (
    AuditEvent,
    MemoryReadEvent,
    MemoryWriteEvent,
    ModelCallEvent,
    RoutingEvent,
    SessionEvent,
    event_to_dict,
)
from arcana.observability.metrics import ArcanaMetrics, get_metrics
from arcana.observability.tracer import configure_tracing, get_tracer

_audit_log: AuditLog | None = None


def configure_observability(base_dir: Path | None = None) -> None:
    """Set up the global audit log and OTel tracing.

    Safe to call multiple times — re-calling replaces the audit log path
    and reconfigures the OTel tracer provider.

    Args:
        base_dir: Root for all observability data. Defaults to ``~/.arcana``.
    """
    global _audit_log

    root = base_dir or (Path.home() / ".arcana")
    log_dir = root / "logs"
    session_dir = log_dir / "sessions"

    _audit_log = AuditLog(log_dir / "audit.jsonl")

    configure_tracing(session_dir)


def get_audit_log() -> AuditLog | None:
    """Return the global AuditLog, or None if configure_observability() has not been called."""
    return _audit_log


__all__ = [
    # Configuration
    "configure_observability",
    "configure_tracing",
    # Getters
    "get_audit_log",
    "get_tracer",
    "get_metrics",
    # Classes
    "AuditLog",
    "ArcanaMetrics",
    # Events
    "AuditEvent",
    "SessionEvent",
    "ModelCallEvent",
    "RoutingEvent",
    "MemoryReadEvent",
    "MemoryWriteEvent",
    "event_to_dict",
]
