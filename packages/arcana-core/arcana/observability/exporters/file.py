"""
File-based OTLP exporters for Phase 1 local observability.

Spans  → ~/.arcana/logs/sessions/{session_id}.jsonl
Metrics → ~/.arcana/metrics/daily.jsonl

These are intentionally simple: each exported batch is appended as JSONL.
The files are human-readable and can be queried directly or via `arcana logs`.

Replace these with OTLPSpanExporter / OTLPMetricExporter for Phase 3 cloud export.
No changes to arcana-core instrumentation are required to make that swap.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ARCANA_HOME = Path.home() / ".arcana"


class FileSpanExporter:
    """
    Writes OTel spans to a per-session JSONL file.
    Implements the opentelemetry-sdk SpanExporter interface.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._path = log_path or ARCANA_HOME / "logs" / "sessions" / "default.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: object) -> object:
        try:
            from opentelemetry.sdk.trace.export import SpanExportResult
            with self._path.open("a", encoding="utf-8") as f:
                for span in spans:  # type: ignore[union-attr]
                    record = {
                        "trace_id":   format(span.context.trace_id, "032x"),
                        "span_id":    format(span.context.span_id, "016x"),
                        "name":       span.name,
                        "start_ns":   span.start_time,
                        "end_ns":     span.end_time,
                        "duration_ms": round((span.end_time - span.start_time) / 1e6, 2)
                                       if span.end_time and span.start_time else None,
                        "status":     span.status.status_code.name,
                        "attributes": dict(span.attributes or {}),
                        "events":     [
                            {"name": e.name, "attributes": dict(e.attributes or {})}
                            for e in span.events
                        ],
                        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
                    }
                    f.write(json.dumps(record, default=str) + "\n")
            return SpanExportResult.SUCCESS
        except Exception:
            try:
                from opentelemetry.sdk.trace.export import SpanExportResult
                return SpanExportResult.FAILURE
            except ImportError:
                return None

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class FileMetricExporter:
    """
    Writes OTel metric snapshots to ~/.arcana/metrics/daily.jsonl.
    Implements the opentelemetry-sdk MetricExporter interface.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._path = log_path or ARCANA_HOME / "metrics" / "daily.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, metrics_data: object, timeout_millis: int = 10_000) -> object:
        try:
            from opentelemetry.sdk.metrics.export import MetricExportResult
            snapshot: dict = {
                "exported_at": datetime.now(tz=timezone.utc).isoformat(),
                "metrics": [],
            }
            for resource_metric in metrics_data.resource_metrics:  # type: ignore[union-attr]
                for scope_metric in resource_metric.scope_metrics:
                    for metric in scope_metric.metrics:
                        entry: dict = {"name": metric.name, "description": metric.description, "data": []}
                        for dp in metric.data.data_points:
                            point: dict = {
                                "attributes": dict(dp.attributes or {}),
                                "time":       dp.time_unix_nano,
                            }
                            if hasattr(dp, "value"):
                                point["value"] = dp.value
                            elif hasattr(dp, "sum"):
                                point["sum"] = dp.sum
                                point["count"] = dp.count
                            entry["data"].append(point)
                        snapshot["metrics"].append(entry)

            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, default=str) + "\n")
            return MetricExportResult.SUCCESS
        except Exception:
            try:
                from opentelemetry.sdk.metrics.export import MetricExportResult
                return MetricExportResult.FAILURE
            except ImportError:
                return None

    def preferred_temporality(self, instrument_type: object) -> object:
        try:
            from opentelemetry.sdk.metrics.export import AggregationTemporality
            return AggregationTemporality.CUMULATIVE
        except ImportError:
            return None

    def preferred_aggregation(self, instrument_type: object) -> object:
        try:
            from opentelemetry.sdk.metrics.view import DefaultAggregation
            return DefaultAggregation()
        except ImportError:
            return None

    def shutdown(self, timeout_millis: int = 30_000) -> object:
        try:
            from opentelemetry.sdk.metrics.export import MetricExportResult
            return MetricExportResult.SUCCESS
        except ImportError:
            return None
