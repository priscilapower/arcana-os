"""KnowledgeConnectorStore — the registry of external read-only memory sources.

A *knowledge connector* is a reference to a folder of Markdown notes — an Obsidian
vault or a plain markdown folder — mounted as a read-only memory tier. This store
persists those references under the ``connectors`` key of
``~/.arcana/connections/memory-adapters.json``. It holds **references only** (a
resolved path, a kind, a name, a scope), never file contents; removing a
connector never touches the folder it points at.

That file is shared: :class:`~arcana.memory.config.MemoryResilienceConfig` reads
per-tier timeout/breaker budgets from its ``private`` / ``shared`` / ``global``
keys. So this store is **key-preserving** — it reads and rewrites only the
``connectors`` key and round-trips every sibling key untouched, so registering a
connector never clobbers a resilience config (and vice versa). Writes are atomic
(temp-then-rename) so a crash mid-save never corrupts the file.
"""

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from arcana.memory.adapters.markdown import MarkdownFolderAdapter
from arcana.memory.errors import MemoryStorageError
from arcana.types import KnowledgeConnector, MemoryType

#: A connector's notes carry no per-note type, so every one defaults to SEMANTIC —
#: durable domain knowledge, the slowest-decaying type — matching how a folder of
#: notes is typically used as a reference tier.
_CONNECTOR_NOTE_TYPE = MemoryType.SEMANTIC


class _RegistryFile(BaseModel):
    """On-disk shape of ``memory-adapters.json``: the ``connectors`` list plus any
    sibling keys (e.g. resilience config), preserved verbatim via ``extra='allow'``
    so a rewrite never drops another component's config from the shared file."""

    model_config = ConfigDict(extra="allow")

    connectors: list[KnowledgeConnector] = []


class KnowledgeConnectorStore:
    """Load, persist, and enumerate registered knowledge connectors.

    One instance owns the ``connectors`` key of one ``memory-adapters.json`` file.
    ``load`` is lazy-tolerant (a missing file is an empty registry); ``add`` /
    ``remove`` mutate in memory and atomically rewrite the file, preserving every
    sibling key.
    """

    def __init__(self, connections_file: Path) -> None:
        self._path = Path(connections_file)
        # The full parsed document, so a rewrite round-trips sibling keys (e.g. the
        # resilience config) that live in the same shared file.
        self._file = _RegistryFile()
        self._connectors: dict[str, KnowledgeConnector] = {}
        self._loaded = False

    def load(self) -> None:
        """Read the registry from disk. A missing file yields an empty registry.

        Idempotent. A structurally invalid file is a real error, surfaced as
        :class:`MemoryStorageError` rather than silently dropped — a corrupt
        registry the user can see beats connectors vanishing. Sibling keys are
        parsed as extras and retained for the next save.
        """
        self._connectors = {}
        self._file = _RegistryFile()
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._file = _RegistryFile.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise MemoryStorageError(f"failed to read connector registry at {self._path}: {exc}") from exc
        for connector in self._file.connectors:
            self._connectors[connector.name] = connector

    def list(self) -> list[KnowledgeConnector]:
        """Every registered connector, sorted by name."""
        self._ensure_loaded()
        return sorted(self._connectors.values(), key=lambda c: c.name)

    def get(self, name: str) -> KnowledgeConnector | None:
        """The connector registered under ``name``, or ``None``."""
        self._ensure_loaded()
        return self._connectors.get(name)

    def add(self, connector: KnowledgeConnector) -> None:
        """Register (or replace) a connector by name and persist the registry."""
        self._ensure_loaded()
        self._connectors[connector.name] = connector
        self._save()

    def remove(self, name: str) -> bool:
        """Unregister ``name``; return whether it existed. Never touches the folder."""
        self._ensure_loaded()
        if name not in self._connectors:
            return False
        del self._connectors[name]
        self._save()
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _save(self) -> None:
        """Atomically rewrite the file, preserving sibling keys (temp-then-rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Update only the connectors key; every other key (resilience tiers, …) is
        # carried through from the parsed document unchanged.
        self._file.connectors = self.list()
        text = self._file.model_dump_json(indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self._path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryStorageError(f"failed to write connector registry at {self._path}: {exc}") from exc


def connector_adapter(connector: KnowledgeConnector, agent_id: UUID) -> MarkdownFolderAdapter:
    """Build the read-only :class:`MarkdownFolderAdapter` for a connector.

    The single place a stored connector reference becomes a live adapter, so the
    health probe and the direct read path build it the same way. Read directly by
    name (not registered as a federation tier), so it carries no scope/pool — query
    it with ``scope=None`` to bypass the adapter's scope short-circuit.
    """
    return MarkdownFolderAdapter(Path(connector.path), agent_id, default_type=_CONNECTOR_NOTE_TYPE)
