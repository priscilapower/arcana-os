"""ConnectionStore — loads ModelConnections from ~/.arcana/connections/models.json."""

import json
import os
from pathlib import Path
from uuid import UUID

from arcana.types.model import ModelConnection, ModelProvider


def resolve_api_key(
    connection_id: UUID | None,
    env_var: str,
    provider_key_name: str,
    *,
    direct: str | None = None,
) -> str | None:
    """Resolve an API key using the canonical four-step precedence.

    1. ``direct`` — explicit key passed at construction time (highest priority).
    2. Connection-id keyring — ``keyring("arcana", "<connection_id>_api_key")``.
    3. Environment variable — ``os.getenv(env_var)``.
    4. Provider-named keyring — ``keyring("arcana", provider_key_name)``.

    Returns the first non-empty value found, or ``None`` if all steps miss.
    Callers that require a key should raise after receiving ``None``.
    """
    if direct:
        return direct
    import keyring

    if connection_id is not None:
        try:
            key = keyring.get_password("arcana", f"{connection_id}_api_key")
            if key:
                return key
        except Exception:
            pass
    key = os.getenv(env_var)
    if key:
        return key
    try:
        key = keyring.get_password("arcana", provider_key_name)
        if key:
            return key
    except Exception:
        pass
    return None


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
        import keyring

        try:
            return keyring.get_password("arcana", f"{connection_id}_api_key")
        except Exception:
            return None

    def all(self) -> list[ModelConnection]:
        return list(self._load())

    def upsert(self, conn: ModelConnection) -> None:
        """Insert conn, or replace the existing connection with the same name."""
        connections = list(self._load())
        idx = next((i for i, c in enumerate(connections) if c.name == conn.name), None)
        if idx is not None:
            connections[idx] = conn.model_copy(update={"id": connections[idx].id})
        else:
            connections.append(conn)
        self._save(connections)

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

    def _save(self, connections: list[ModelConnection]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([c.model_dump(mode="json") for c in connections], indent=2))
        self._connections = connections
