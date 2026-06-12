"""Phase 3: OTLP export helper.

Requires: pip install opentelemetry-exporter-otlp-proto-http
"""


def configure_otlp_tracing(endpoint: str, headers: dict[str, str] | None = None) -> None:
    """Configure OTel to export traces via OTLP (e.g. Grafana Cloud / Tempo).

    Phase 3 feature. Example::

        configure_otlp_tracing(
            endpoint="https://otlp-gateway-prod-eu-west-0.grafana.net/otlp",
            headers={"Authorization": "Basic <token>"},
        )
    """
    try:
        from opentelemetry import trace  # type: ignore[import]
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # type: ignore[import]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("OTLP export requires: pip install opentelemetry-exporter-otlp-proto-http") from exc

    exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers or {})  # pyright: ignore[reportUnknownVariableType]
    provider = TracerProvider()  # pyright: ignore[reportUnknownVariableType]
    provider.add_span_processor(BatchSpanProcessor(exporter))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]
    trace.set_tracer_provider(provider)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
