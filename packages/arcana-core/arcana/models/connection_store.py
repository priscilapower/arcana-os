"""ConnectionStore — loads ModelConnections from ~/.arcana/connections/models.json."""

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from arcana.types._utils import now_utc
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

    def get_by_id(self, connection_id: UUID) -> ModelConnection | None:
        return next((c for c in self._load() if c.id == connection_id), None)

    def get_api_key(self, connection_id: UUID) -> str | None:
        import keyring

        try:
            return keyring.get_password("arcana", f"{connection_id}_api_key")
        except Exception:
            return None

    def all(self) -> list[ModelConnection]:
        return list(self._load())

    def upsert(self, conn: ModelConnection) -> None:
        """Insert conn, or replace the existing provider connection with the same name.

        Always updates ``updated_at`` to now on write.
        """
        connections = list(self._load())
        idx = next((i for i, c in enumerate(connections) if c.name == conn.name), None)
        updated = conn.model_copy(update={"updated_at": now_utc()})
        if idx is not None:
            updated = updated.model_copy(update={"id": connections[idx].id})
            connections[idx] = updated
        else:
            connections.append(updated)
        self._save(connections)

    def delete(self, name: str) -> None:
        """Remove the connection with the given name and delete its keyring credential."""
        all_conns = list(self._load())
        to_delete = next((c for c in all_conns if c.name == name), None)
        remaining = [c for c in all_conns if c.name != name]
        self._save(remaining)
        if to_delete is not None:
            ref = to_delete.credential_ref or f"{to_delete.id}_api_key"
            try:
                self.delete_credential(ref)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    def set_credential(self, ref: str, secret: str) -> None:
        """Write a secret to the OS keyring under the given ref key."""
        import keyring

        keyring.set_password("arcana", ref, secret)

    def delete_credential(self, ref: str) -> None:
        """Delete a secret from the OS keyring. No-op if the entry does not exist."""
        import keyring

        try:
            keyring.delete_password("arcana", ref)
        except Exception:
            pass

    def reload(self) -> None:
        self._connections = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> list[ModelConnection]:
        if self._connections is None:
            if self._path.exists():
                raw = json.loads(self._path.read_text())
                connections: list[ModelConnection] = []
                needs_save = False
                for c in raw:
                    if "model_id" in c and "default_model" not in c:
                        c = dict(c)
                        c["default_model"] = c.pop("model_id")
                        needs_save = True
                    connections.append(ModelConnection.model_validate(c))
                self._connections = connections
                if needs_save:
                    self._save(self._connections)
            else:
                self._connections = []
        return self._connections

    def _save(self, connections: list[ModelConnection]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps([c.model_dump(mode="json") for c in connections], indent=2).encode()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, prefix=".models_", suffix=".tmp")
        try:
            os.write(tmp_fd, data)
            os.close(tmp_fd)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        self._connections = connections
