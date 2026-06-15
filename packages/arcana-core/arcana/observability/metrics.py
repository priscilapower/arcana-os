"""OTel meter setup with no-op fallback for when opentelemetry is not installed."""

from typing import Any

# ---------------------------------------------------------------------------
# No-op fallbacks
# ---------------------------------------------------------------------------


class _NoOpCounter:
    def add(self, _amount: int | float, _attributes: dict[str, Any] | None = None) -> None:
        pass


class _NoOpHistogram:
    def record(self, _amount: int | float, _attributes: dict[str, Any] | None = None) -> None:
        pass


class _NoOpMeter:
    def create_counter(self, _name: str, **_kw: Any) -> _NoOpCounter:
        return _NoOpCounter()

    def create_histogram(self, _name: str, **_kw: Any) -> _NoOpHistogram:
        return _NoOpHistogram()


def _get_otel_meter(name: str) -> Any:
    try:
        from opentelemetry import metrics  # type: ignore[import]

        return metrics.get_meter(name)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    except ImportError:
        return _NoOpMeter()


# ---------------------------------------------------------------------------
# Instrument collection
# ---------------------------------------------------------------------------


class ArcanaMetrics:
    """All OTel metric instruments used by arcana. One instance per process."""

    def __init__(self) -> None:
        meter = _get_otel_meter("arcana")
        self.sessions_total = meter.create_counter(
            "arcana.sessions.total",
            description="Total number of agent sessions",
        )
        self.input_tokens = meter.create_counter(
            "arcana.model.tokens.input",
            description="Total input tokens consumed",
        )
        self.output_tokens = meter.create_counter(
            "arcana.model.tokens.output",
            description="Total output tokens generated",
        )
        self.session_duration = meter.create_histogram(
            "arcana.session.duration_ms",
            description="Session duration in milliseconds",
            unit="ms",
        )
        self.model_latency = meter.create_histogram(
            "arcana.model.latency_ms",
            description="LLM call latency in milliseconds",
            unit="ms",
        )

    def record_session(
        self,
        *,
        card: str,
        model: str,
        status: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
    ) -> None:
        attrs = {"card": card, "model": model, "status": status}
        self.sessions_total.add(1, attrs)
        self.input_tokens.add(input_tokens, {"model": model, "card": card})
        self.output_tokens.add(output_tokens, {"model": model, "card": card})
        self.session_duration.record(duration_ms, attrs)

    def record_model_call(self, *, model: str, latency_ms: int, success: bool) -> None:
        attrs = {"model": model, "success": str(success).lower()}
        self.model_latency.record(latency_ms, attrs)


_metrics: ArcanaMetrics | None = None


def get_metrics() -> ArcanaMetrics:
    """Return the process-wide ArcanaMetrics instance, creating it on first call."""
    global _metrics
    if _metrics is None:
        _metrics = ArcanaMetrics()
    return _metrics
