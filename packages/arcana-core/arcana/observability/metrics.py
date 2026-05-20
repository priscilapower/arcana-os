"""
OpenTelemetry metrics wiring for Arcana.

All metric instruments are defined here as module-level singletons.
Import the instrument you need; recording is one line at the call site.

Phase 1: metrics flushed to ~/.arcana/metrics/daily.jsonl every 60 s
Phase 3: swap FileMetricExporter for OTLPMetricExporter

Key health signal:
  routing_fallbacks / routing_decisions > 0.30
  → The World's rules are not covering the prompt space.
"""

from __future__ import annotations

try:
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False


def configure_metrics(export_interval_seconds: int = 60) -> object | None:
    """
    Configure periodic metric export to ~/.arcana/metrics/daily.jsonl.
    No-ops gracefully if opentelemetry-sdk is not installed.
    """
    if not _OTEL_AVAILABLE:
        return None

    from arcana.observability.exporters.file import FileMetricExporter

    reader = PeriodicExportingMetricReader(
        FileMetricExporter(),
        export_interval_millis=export_interval_seconds * 1_000,
    )
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    _init_instruments()
    return provider


def _init_instruments() -> None:
    """Create all metric instruments against the active MeterProvider."""
    if not _OTEL_AVAILABLE:
        return

    global sessions_total, model_tokens_input, model_tokens_output
    global memory_writes_total, memory_promotions
    global routing_decisions, routing_fallbacks
    global tool_calls_total, tool_failures_total
    global session_duration_ms, model_latency_ms
    global memory_search_ms, routing_latency_ms

    meter = metrics.get_meter("arcana")

    # Counters — monotonically increasing
    sessions_total       = meter.create_counter(
        "arcana.sessions.total",
        description="Total sessions started",
    )
    model_tokens_input   = meter.create_counter(
        "arcana.model.tokens.input",
        description="Total input tokens sent to LLMs",
    )
    model_tokens_output  = meter.create_counter(
        "arcana.model.tokens.output",
        description="Total output tokens received from LLMs",
    )
    memory_writes_total  = meter.create_counter(
        "arcana.memory.writes.total",
        description="Total memory entries written across all tiers",
    )
    memory_promotions    = meter.create_counter(
        "arcana.memory.promotions.global",
        description="Memory entries auto-promoted to GLOBAL tier (importance >= 0.9)",
    )
    routing_decisions    = meter.create_counter(
        "arcana.routing.decisions.total",
        description="Total routing decisions made by The World",
    )
    routing_fallbacks    = meter.create_counter(
        "arcana.routing.decisions.fallback",
        description="Routing decisions that fell back to the default agent (no rule matched)",
    )
    tool_calls_total     = meter.create_counter(
        "arcana.tools.calls.total",
        description="Total tool executions across all agents",
    )
    tool_failures_total  = meter.create_counter(
        "arcana.tools.calls.failed",
        description="Tool executions that returned an error",
    )

    # Histograms — distribution over time
    session_duration_ms  = meter.create_histogram(
        "arcana.session.duration_ms",
        description="End-to-end session duration in milliseconds",
    )
    model_latency_ms     = meter.create_histogram(
        "arcana.model.latency_ms",
        description="LLM completion latency in milliseconds",
    )
    memory_search_ms     = meter.create_histogram(
        "arcana.memory.search_ms",
        description="MemoryFederation.search() fan-out duration in milliseconds",
    )
    routing_latency_ms   = meter.create_histogram(
        "arcana.routing.latency_ms",
        description="Time for The World to make a routing decision",
    )


# Module-level placeholders — replaced by _init_instruments() after configure_metrics()
class _NoOpInstrument:
    def add(self, *a, **kw) -> None: pass
    def record(self, *a, **kw) -> None: pass


sessions_total      = _NoOpInstrument()
model_tokens_input  = _NoOpInstrument()
model_tokens_output = _NoOpInstrument()
memory_writes_total = _NoOpInstrument()
memory_promotions   = _NoOpInstrument()
routing_decisions   = _NoOpInstrument()
routing_fallbacks   = _NoOpInstrument()
tool_calls_total    = _NoOpInstrument()
tool_failures_total = _NoOpInstrument()
session_duration_ms = _NoOpInstrument()
model_latency_ms    = _NoOpInstrument()
memory_search_ms    = _NoOpInstrument()
routing_latency_ms  = _NoOpInstrument()
