"""
OTLP exporter wiring for Phase 3 cloud observability.

Swap configure_local_tracing() → configure_otlp_tracing() in bootstrap.py
and all arcana-core instrumentation continues to work unchanged.

Recommended Phase 3 backend: Grafana Cloud
  - Traces via Tempo (OTLP HTTP)
  - Metrics via Mimir (OTLP HTTP)
  - Logs via Loki (OTLP HTTP)
  - All OTLP-native; no vendor lock-in

Usage:
    from arcana.observability.exporters.otlp import (
        configure_otlp_tracing,
        configure_otlp_metrics,
    )
    configure_otlp_tracing(
        endpoint="https://otlp-gateway-prod-eu-west-0.grafana.net/otlp",
        headers={"Authorization": "Basic <base64-encoded-instanceid:token>"},
    )
    configure_otlp_metrics(
        endpoint="https://otlp-gateway-prod-eu-west-0.grafana.net/otlp",
        headers={"Authorization": "Basic <base64-encoded-instanceid:token>"},
    )
"""

from __future__ import annotations


def configure_otlp_tracing(
    endpoint: str,
    headers: dict[str, str],
    service_name: str = "arcana-core",
) -> None:
    """
    Configure OTel tracing with OTLP HTTP export.
    Requires: opentelemetry-exporter-otlp-proto-http
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service_name})
    exporter = OTLPSpanExporter(
        endpoint=f"{endpoint.rstrip('/')}/v1/traces",
        headers=headers,
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def configure_otlp_metrics(
    endpoint: str,
    headers: dict[str, str],
    service_name: str = "arcana-core",
    export_interval_seconds: int = 60,
) -> None:
    """
    Configure OTel metrics with OTLP HTTP export.
    Requires: opentelemetry-exporter-otlp-proto-http
    """
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service_name})
    exporter = OTLPMetricExporter(
        endpoint=f"{endpoint.rstrip('/')}/v1/metrics",
        headers=headers,
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=export_interval_seconds * 1_000,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    # Re-init instruments against the new provider
    from arcana.observability.metrics import _init_instruments
    _init_instruments()
