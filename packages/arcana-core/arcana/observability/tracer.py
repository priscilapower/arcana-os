"""
OpenTelemetry tracing wiring for Arcana.

Phase 1: spans written to ~/.arcana/logs/sessions/{session_id}.jsonl
Phase 3: swap FileSpanExporter for OTLPSpanExporter — zero changes to
         arcana-core instrumentation required.

Instrumentation lives in the adapter boundaries (MemoryFederation.search,
Agent.run, MCPRegistry.resolve) — not scattered through business logic.
"""

from __future__ import annotations

from pathlib import Path

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


ARCANA_HOME = Path.home() / ".arcana"


def configure_local_tracing(session_id: str = "") -> object | None:
    """
    Configure file-based span export for Phase 1.

    If opentelemetry-sdk is not installed, installs a no-op tracer silently —
    so arcana-core works without the optional observability deps.

    Args:
        session_id: used to name the per-session span file.
    """
    if not _OTEL_AVAILABLE:
        return None

    from arcana.observability.exporters.file import FileSpanExporter

    log_path = ARCANA_HOME / "logs" / "sessions" / f"{session_id or 'default'}.jsonl"
    exporter = FileSpanExporter(log_path=log_path)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def get_tracer(name: str = "arcana") -> object:
    """
    Return the active OTel tracer, or a no-op tracer if OTel is not installed.
    Callers should always go through this function — never import trace directly.
    """
    if not _OTEL_AVAILABLE:
        return _NoOpTracer()
    return trace.get_tracer(name)


class _NoOpTracer:
    """Fallback when opentelemetry-sdk is not installed."""

    def start_as_current_span(self, name: str, **kwargs):  # noqa: ANN001
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield None

        return _noop()
