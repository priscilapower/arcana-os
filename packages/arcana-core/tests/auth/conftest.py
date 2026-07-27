"""Fixtures for the auth suite: a fake authorization server and an in-memory keyring.

Everything is hermetic — no browser, socket to the outside, real keychain, or DNS.
The fake AS is an ``httpx.MockTransport`` handler wired into an ``OAuthClient``; the
SSRF guard's resolver is patched to a public IP so guarded discovery calls proceed
without touching real DNS (the SSRF *rejection* tests use IP literals and need no patch).
"""

from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import keyring
import pytest

import arcana.auth.oauth as oauth_mod
from arcana.auth.oauth import OAuthClient

# A public, non-DNS IP the SSRF guard treats as safe.
PUBLIC_IP = "93.184.216.34"

ISSUER = "https://as.example.test"
AUTH_ENDPOINT = f"{ISSUER}/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/token"
REGISTRATION_ENDPOINT = f"{ISSUER}/register"
DEVICE_ENDPOINT = f"{ISSUER}/device"
METADATA_PATH = "/.well-known/oauth-authorization-server"


class FakeAuthServer:
    """A scriptable OAuth authorization server over ``httpx.MockTransport``.

    Records the requests it receives (so a test can assert the grant/params sent)
    and returns canned metadata / registration / token / device responses. The
    token endpoint counts calls so a test can pin "refresh happened exactly once".
    """

    def __init__(self) -> None:
        self.token_calls: list[dict[str, list[str]]] = []
        self.device_poll_count = 0
        self.metadata = {
            "issuer": ISSUER,
            "authorization_endpoint": AUTH_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
            "registration_endpoint": REGISTRATION_ENDPOINT,
            "device_authorization_endpoint": DEVICE_ENDPOINT,
            "scopes_supported": ["read", "write"],
        }
        # A token body the token endpoint returns; a test may swap it.
        self.token_body: dict[str, object] = {
            "access_token": "access-1",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "refresh-1",
            "scope": "read write",
        }
        # Device grant: how many 'authorization_pending' polls before success.
        self.device_pending_polls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == METADATA_PATH:
            return httpx.Response(200, json=self.metadata)
        if path == "/register":
            return httpx.Response(200, json={"client_id": "dyn-client-123"})
        if path == "/device":
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-code",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://as.example.test/activate",
                    "expires_in": 300,
                    "interval": 1,
                },
            )
        if path == "/token":
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            if form.get("grant_type") == ["urn:ietf:params:oauth:grant-type:device_code"]:
                self.device_poll_count += 1
                if self.device_poll_count <= self.device_pending_polls:
                    return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json=self.token_body)
        return httpx.Response(404, json={"error": "not_found"})

    def client(self) -> OAuthClient:
        transport = httpx.MockTransport(self.handler)
        return OAuthClient(http=httpx.AsyncClient(transport=transport, follow_redirects=False))


@pytest.fixture
def fake_as(monkeypatch: pytest.MonkeyPatch) -> FakeAuthServer:
    """A fake AS with the SSRF resolver patched to a public IP (no real DNS)."""
    monkeypatch.setattr(oauth_mod, "_resolve_addrs", lambda _host: [PUBLIC_IP])
    return FakeAuthServer()


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """An in-memory keyring — no OS keychain touched. Returns the backing store."""
    store: dict[str, str] = {}

    def _set(service: str, ref: str, secret: str) -> None:
        store[f"{service}/{ref}"] = secret

    def _get(service: str, ref: str) -> str | None:
        return store.get(f"{service}/{ref}")

    def _delete(service: str, ref: str) -> None:
        store.pop(f"{service}/{ref}", None)

    monkeypatch.setattr(keyring, "set_password", _set)
    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "delete_password", _delete)
    return store


def open_browser_capturing(sink: list[str]) -> Callable[[str], None]:
    """A BrowserOpener that records the authorize URL instead of opening one."""

    def _open(url: str) -> None:
        sink.append(url)

    return _open
