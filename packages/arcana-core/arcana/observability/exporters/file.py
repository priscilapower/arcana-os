"""FileSpanExporter — writes OTel spans as JSONL to ~/.arcana/logs/sessions/.

Requires: pip install arcana-core[observability]
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class FileSpanExporter:
    """Writes each OTel span as a JSONL line to {session_dir}/{trace_id}.jsonl."""

    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir
        self._session_dir.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[Any]) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult  # type: ignore[import]

        try:
            for span in spans:
                trace_id = format(span.context.trace_id, "032x")
                path = self._session_dir / f"{trace_id}.jsonl"
                span_events: list[Any] = list(span.events) if span.events else []
                record: dict[str, Any] = {
                    "name": span.name,
                    "trace_id": trace_id,
                    "span_id": format(span.context.span_id, "016x"),
                    "parent_span_id": (format(span.parent.span_id, "016x") if span.parent else None),
                    "start_time_ns": span.start_time,
                    "end_time_ns": span.end_time,
                    "duration_ns": ((span.end_time or 0) - (span.start_time or 0)),
                    "attributes": dict(span.attributes or {}),
                    "status": span.status.status_code.name if span.status else None,
                    "events": [
                        {
                            "name": e.name,
                            "timestamp_ns": e.timestamp,
                            "attributes": dict(e.attributes or {}),
                        }
                        for e in span_events
                    ],
                }
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            return SpanExportResult.SUCCESS  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        except Exception:
            return SpanExportResult.FAILURE  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        return True
