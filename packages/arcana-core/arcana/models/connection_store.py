"""ConnectionStore — loads ModelConnections from ~/.arcana/connections/models.json."""

import json
from pathlib import Path
from uuid import UUID

import keyring

from arcana.types.model import ModelConnection, ModelProvider


def _default_path() -> Path:
    return Path.home() / ".arcana" / "connections" / "models.json"


class ConnectionStore:
    """
    Reads ModelConnection records from disk and credentials from the OS keyring.

    Connections are loaded lazily on first access; call ``reload()`` to invalidate
    the cache if the file changes at runtime.

    Usage::

        store = ConnectionStore()
        conn = store.get_by_provider(ModelProvider.ANTHROPIC)
        key  = store.get_api_key(conn.id)
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._connections: list[ModelConnection] | None = None

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_by_provider(self, provider: ModelProvider | str) -> ModelConnection | None:
        target = str(provider)
        return next((c for c in self._load() if str(c.provider) == target), None)

    def get_by_name(self, name: str) -> ModelConnection | None:
        return next((c for c in self._load() if c.name == name), None)

    def get_api_key(self, connection_id: UUID) -> str | None:
        try:
            return keyring.get_password("arcana", f"{connection_id}_api_key")
        except Exception:
            return None

    def all(self) -> list[ModelConnection]:
        return list(self._load())

    def reload(self) -> None:
        self._connections = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> list[ModelConnection]:
        if self._connections is None:
            if self._path.exists():
                raw = json.loads(self._path.read_text())
                self._connections = [ModelConnection.model_validate(c) for c in raw]
            else:
                self._connections = []
        return self._connections
