"""
Arcana observability — logs, traces, metrics, audit.

All data written locally to ~/.arcana/logs/ and ~/.arcana/metrics/.

Usage (in SessionManager or Agent.run):

    from arcana.observability import configure_observability, get_audit_log

    configure_observability(session_id=str(session.id))
    audit = get_audit_log()
    audit.write(RoutingEvent(...))
"""

from __future__ import annotations

from arcana.observability.audit import AuditLog
from arcana.observability.tracer import configure_local_tracing, get_tracer
from arcana.observability.metrics import configure_metrics

_audit_log: AuditLog | None = None


def configure_observability(session_id: str = "") -> None:
    """
    Wire up all observability backends for the current process.

    Call once at process start (CLI) or once per session (server).

    Args:
        session_id: Used to scope the per-session span file.
    """
    global _audit_log
    _audit_log = AuditLog()
    configure_local_tracing(session_id=session_id)
    configure_metrics()


def get_audit_log() -> AuditLog:
    """Return the process-level AuditLog instance (auto-initialised if needed)."""
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog()
    return _audit_log


__all__ = [
    "configure_observability",
    "get_audit_log",
    "get_tracer",
    "AuditLog",
]
