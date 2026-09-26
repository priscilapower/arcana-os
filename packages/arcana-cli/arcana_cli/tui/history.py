"""Per-agent chat input history, stored in prompt_toolkit's ``FileHistory`` format.

Each agent's history lives at ``ARCANA_HOME/agents/<uuid>/chat_history``. The
file format is prompt_toolkit's, read and written here without importing it, so
existing history files keep working and the two editors can share a file::

    <blank line>
    # 2026-09-27 10:00:00.123456
    +first line of an entry
    +second line of the same entry

Every ``+`` line belongs to the entry being read; any other line ends it.

History never stops the input from working: a file that can't be read starts
the history empty, and a file that can't be written leaves the history
in-memory for the rest of the session. Neither raises.

* :class:`AgentHistory` — the entries, append-only persistence, the history
  ghost suggestion, and reverse search.
* :class:`HistoryCursor` — Up/Down recall over an :class:`AgentHistory`, keeping
  the draft being typed and any edits to recalled entries until the next submit.
"""

import datetime
import io
import os
from pathlib import Path
from typing import Self
from uuid import UUID

from arcana_cli.constants import ARCANA_HOME

#: The history file's name inside an agent's directory.
HISTORY_FILENAME = "chat_history"

# A new history file is readable by its owner only: chat input can hold secrets.
_FILE_MODE = 0o600


def agent_history_path(agent_id: UUID | str, home: Path | None = None) -> Path:
    """``<home>/agents/<uuid>/chat_history`` for ``agent_id``, ``home`` defaulting to ``ARCANA_HOME``.

    The id is parsed as a UUID and written in canonical form, so a crafted id
    can't steer the path outside the agent's directory; anything that isn't a
    UUID raises :class:`ValueError`.
    """
    uid = agent_id if isinstance(agent_id, UUID) else UUID(agent_id)
    return (home if home is not None else ARCANA_HOME) / "agents" / str(uid) / HISTORY_FILENAME


def parse_history(data: bytes) -> list[str]:
    """The entries in a ``FileHistory``-format file, oldest first."""
    entries: list[str] = []
    lines: list[str] = []
    for raw in io.BytesIO(data):
        line = raw.decode("utf-8", errors="replace")
        if line.startswith("+"):
            lines.append(line[1:])
            continue
        if lines:
            entries.append("".join(lines)[:-1])  # drop the entry's final newline
        lines = []
    if lines:
        entries.append("".join(lines)[:-1])
    return entries


def format_entry(text: str, when: datetime.datetime) -> bytes:
    """One entry in ``FileHistory`` format, as appended to the file."""
    body = "".join(f"+{line}\n" for line in text.split("\n"))
    return f"\n# {when}\n{body}".encode()


class AgentHistory:
    """An agent's submitted inputs, oldest first, persisted append-only when it has a file.

    ``path=None`` is a history that lives in memory only.
    """

    def __init__(self, path: Path | None = None, entries: list[str] | None = None) -> None:
        self._path = path
        self._entries: list[str] = list(entries) if entries is not None else []
        if path is not None and entries is None:
            try:
                self._entries = parse_history(path.read_bytes()) if path.exists() else []
            except OSError:
                self._path = None

    @classmethod
    def for_agent(cls, agent_id: UUID | str, home: Path | None = None) -> Self:
        """The history of ``agent_id`` under ``home`` (default ``ARCANA_HOME``); see :func:`agent_history_path`."""
        return cls(agent_history_path(agent_id, home))

    @property
    def path(self) -> Path | None:
        """The file entries are appended to, or ``None`` once the history is in-memory."""
        return self._path

    @property
    def entries(self) -> tuple[str, ...]:
        """Every entry, oldest first."""
        return tuple(self._entries)

    def append(self, text: str) -> None:
        """Record a submitted ``text``; blank text and a repeat of the newest entry are skipped.

        A failed write switches the history to in-memory; the entry is still kept.
        """
        if not text or (self._entries and self._entries[-1] == text):
            return
        self._entries.append(text)
        if self._path is None:
            return
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE)
            with os.fdopen(fd, "ab") as f:
                f.write(format_entry(text, datetime.datetime.now()))
        except OSError:
            self._path = None

    def suggest(self, text: str) -> str:
        """The ghost-text completion for ``text``: the rest of the newest history line its last line starts.

        Only the last line of ``text`` is matched, against every line of every
        entry, newest first. Blank input suggests nothing.
        """
        last = text.rsplit("\n", 1)[-1]
        if not last.strip():
            return ""
        for entry in reversed(self._entries):
            for line in reversed(entry.splitlines()):
                if line.startswith(last):
                    return line[len(last) :]
        return ""

    def search(self, query: str, before: int | None = None) -> int | None:
        """The index of the newest entry containing ``query``, older than index ``before`` if given."""
        if not query:
            return None
        start = len(self._entries) if before is None else min(before, len(self._entries))
        for index in range(start - 1, -1, -1):
            if query in self._entries[index]:
                return index
        return None


class HistoryCursor:
    """Up/Down recall over an :class:`AgentHistory`.

    The first step back saves the text being typed as the draft; stepping
    forward past the newest entry brings it back. Edits made to a recalled entry
    are kept while walking, as prompt_toolkit does, until :meth:`reset`.
    """

    def __init__(self, history: AgentHistory) -> None:
        self._history = history
        self._index: int | None = None
        self._edits: dict[int, str] = {}

    @property
    def browsing(self) -> bool:
        """Whether a history entry, rather than the draft, is showing."""
        return self._index is not None

    def reset(self) -> None:
        """Forget the draft and any edits; the next step back starts at the newest entry."""
        self._index = None
        self._edits.clear()

    def older(self, current: str) -> str | None:
        """Step back from ``current`` (the text showing); the older text, or ``None`` at the oldest entry."""
        entries = self._history.entries
        index = len(entries) if self._index is None else self._index
        if index == 0:
            return None
        self._edits[index] = current
        self._index = index - 1
        return self._edits.get(self._index, entries[self._index])

    def newer(self, current: str) -> str | None:
        """Step forward from ``current``; the newer text or the draft, or ``None`` when already on the draft."""
        if self._index is None:
            return None
        entries = self._history.entries
        self._edits[self._index] = current
        index = self._index + 1
        self._index = index if index < len(entries) else None
        return self._edits.get(index, entries[index] if index < len(entries) else "")
