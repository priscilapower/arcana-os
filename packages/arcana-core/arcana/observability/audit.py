"""Append-only JSONL audit log. No OTel dependency."""

import json
from pathlib import Path
from typing import Any

from arcana.observability.events import AuditEvent, event_to_dict


class AuditLog:
    """Append-only JSONL log. Each event is one line.

    Errors during write are silently swallowed — observability must never
    break the main agent call path.
    """

    DEFAULT_PATH: Path = Path.home() / ".arcana" / "logs" / "audit.jsonl"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: AuditEvent) -> None:
        """Serialize event to JSONL and append. Non-fatal on I/O error."""
        try:
            line = json.dumps(event_to_dict(event), default=str) + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def tail(self, n: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        """Return the last n events, optionally filtered by type."""
        if not self._path.exists():
            return []
        with open(self._path, encoding="utf-8") as f:
            lines = f.readlines()

        collected: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type is None or event.get("type") == event_type:
                collected.append(event)
                if len(collected) >= n:
                    break
        return list(reversed(collected))

    def clear(self) -> None:
        """Delete the log file. Useful in tests."""
        if self._path.exists():
            self._path.unlink()
