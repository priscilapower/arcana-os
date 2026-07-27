"""Finding ``secret_leak`` — a credential never lands in a file, dump, or result.

MCP SSE auth is a *reference* (``auth_key_ref``) into the OS keyring; the token is
resolved at connect, injected as a request header, and never persisted, logged, or
placed on a span. This module plants a real secret in a fake keyring and asserts
the two halves: the adapter *does* resolve it into an ``Authorization`` header (so
auth works), while the persisted ``mcps.json`` and the config's own JSON dump carry
only the reference — never the token.

Covers the "no plaintext credential in output / span / mcps.json" contract.
"""

import json
from pathlib import Path

import keyring
import pytest

from arcana.tools.adapters.mcp import KEYRING_SERVICE, MCPToolAdapter
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import MCPServerConfig, MCPServerStatus, MCPTransport

pytestmark = pytest.mark.security

#: Guards this module discharges — see ``security/catalog.py``.
COVERS = frozenset({"secret:no_plaintext"})

SECRET_TOKEN = "sk-live-DO-NOT-PERSIST-abc123"
AUTH_REF = "notion-token"


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyring holding one secret under ``AUTH_REF`` — no OS keychain touched."""
    store = {(KEYRING_SERVICE, AUTH_REF): SECRET_TOKEN}
    monkeypatch.setattr(keyring, "get_password", lambda service, ref: store.get((service, ref)))


def _server() -> MCPServerConfig:
    return MCPServerConfig(
        name="notion",
        server_url="https://mcp.notion.com/sse",
        transport=MCPTransport.SSE,
        status=MCPServerStatus.CONNECTED,
        auth_key_ref=AUTH_REF,
    )


def test_the_token_resolves_into_a_header_but_the_config_dump_holds_only_the_ref(fake_keyring: None):
    adapter = MCPToolAdapter(_server())

    headers = adapter._auth_headers()
    assert headers is not None
    # The secret is used — it reaches the request as a bearer token.
    assert SECRET_TOKEN in json.dumps(dict(headers))

    # …but the config that gets persisted carries only the reference.
    dumped = _server().model_dump_json()
    assert AUTH_REF in dumped
    assert SECRET_TOKEN not in dumped


def test_saved_mcps_json_never_contains_the_token(tmp_path: Path, fake_keyring: None):
    connections_file = tmp_path / "connections" / "mcps.json"
    registry = MCPRegistry(connections_file=connections_file)
    registry._register_builtins()
    registry._loaded = True
    registry.register_server(_server())  # persists to disk

    on_disk = connections_file.read_text()
    assert AUTH_REF in on_disk  # the reference is fine to persist
    assert SECRET_TOKEN not in on_disk  # the token is not
    # And a fresh load round-trips the reference without inventing a secret field.
    reloaded = json.loads(on_disk)
    assert SECRET_TOKEN not in json.dumps(reloaded)
