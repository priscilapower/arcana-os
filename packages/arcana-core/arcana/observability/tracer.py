"""OTel tracer setup with no-op fallback for when opentelemetry is not installed."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# No-op fallbacks — used when opentelemetry-api is not installed
# ---------------------------------------------------------------------------


class _NoOpSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:
        pass

    def record_exception(self, _exc: Exception | None, **_kw: Any) -> None:
        pass

    def set_status(self, *_args: Any, **_kw: Any) -> None:
        pass

    def add_event(self, _name: str, **_kw: Any) -> None:
        pass

    def end(self) -> None:
        pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Generator[_NoOpSpan, None, None]:
        yield _NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()


_NOOP = _NoOpTracer()
_NOOP_SPAN = _NoOpSpan()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def configure_tracing(session_dir: Path) -> None:
    """Configure OTel with a FileSpanExporter writing to session_dir.

    No-op if opentelemetry-sdk is not installed. Install via:
        pip install arcana-core[observability]
    """
    try:
        from opentelemetry import trace  # type: ignore[import]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # type: ignore[import]

        from arcana.observability.exporters.file import FileSpanExporter

        provider = TracerProvider()  # pyright: ignore[reportUnknownVariableType]
        provider.add_span_processor(  # pyright: ignore[reportUnknownMemberType]
            SimpleSpanProcessor(FileSpanExporter(session_dir))  # pyright: ignore[reportUnknownVariableType,reportArgumentType]
        )
        trace.set_tracer_provider(provider)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    except ImportError:
        pass


def get_tracer(name: str = "arcana") -> Any:
    """Return an OTel Tracer, or a no-op tracer if opentelemetry-api is not installed."""
    try:
        from opentelemetry import trace  # type: ignore[import]

        return trace.get_tracer(name)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    except ImportError:
        return _NOOP


def get_current_span() -> Any:
    """Return the active span so callers can annotate it, or a no-op span.

    Lets code deep in a call stack add attributes to the span a caller opened
    (e.g. a tool handler enriching the gateway's ``tool.dispatch`` span) without
    threading the span object through. Falls back to a no-op when
    opentelemetry-api is not installed or no span is active.
    """
    try:
        from opentelemetry import trace  # type: ignore[import]

        return trace.get_current_span()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    except ImportError:
        return _NOOP_SPAN
