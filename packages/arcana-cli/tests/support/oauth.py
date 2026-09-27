"""A fake OAuth authorization server for the CLI's sign-in tests, and a stand-in browser.

:func:`install_fake_as` routes every :class:`~arcana.auth.OAuthClient` the CLI
builds to a scriptable authorization server over ``httpx.MockTransport``, and
patches the SSRF guard's resolver to a public IP so no real DNS is touched. The
loopback flow still runs its real ``127.0.0.1`` listener: :class:`FakeBrowser`
stands in for ``webbrowser.open`` and, when told to, completes the redirect by
calling that listener, as a browser would.
"""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

import arcana.auth.oauth as core_oauth
import arcana_cli._oauth as cli_oauth
from arcana.auth import OAuthClient

ISSUER = "https://as.example.test"
#: The device code's user code, shown to the user.
USER_CODE = "WXYZ-1234"
#: The access token the fake server issues; it must never reach the transcript.
ACCESS_TOKEN = "access-token-never-shown"
REFRESH_TOKEN = "refresh-token-never-shown"


class FakeAuthServer:
    """Metadata, dynamic registration, device and token endpoints; the device grant stays pending until approved."""

    def __init__(self) -> None:
        self.approved = False
        self.token_calls: list[dict[str, list[str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "registration_endpoint": f"{ISSUER}/register",
                    "device_authorization_endpoint": f"{ISSUER}/device",
                    "scopes_supported": ["read"],
                },
            )
        if path == "/register":
            return httpx.Response(200, json={"client_id": "dyn-client"})
        if path == "/device":
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-code",
                    "user_code": USER_CODE,
                    "verification_uri": f"{ISSUER}/activate",
                    "expires_in": 300,
                    "interval": 0,  # falls back to DEVICE_POLL_INTERVAL_S, patched short
                },
            )
        if path == "/token":
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            if form.get("grant_type") == ["urn:ietf:params:oauth:grant-type:device_code"] and not self.approved:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": REFRESH_TOKEN,
                },
            )
        return httpx.Response(404)

    def client(self) -> OAuthClient:
        return OAuthClient(http=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


class FakeBrowser:
    """Records each authorize URL; with ``complete`` set, redirects back to the loopback listener like a browser."""

    def __init__(self, *, complete: bool) -> None:
        self.complete = complete
        self.opened: list[str] = []
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def redirect_uri(self) -> str:
        return httpx.URL(self.opened[-1]).params["redirect_uri"]

    def open(self, url: str) -> bool:
        self.opened.append(url)
        if self.complete:
            state = httpx.URL(url).params["state"]
            self._tasks.append(asyncio.ensure_future(self._redirect(self.redirect_uri, state)))
        return True

    @staticmethod
    async def _redirect(redirect_uri: str, state: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.get(redirect_uri, params={"code": "the-code", "state": state})


def install_fake_as(
    monkeypatch: pytest.MonkeyPatch, *, complete_browser: bool = True
) -> tuple[FakeAuthServer, FakeBrowser]:
    """Point the CLI's sign-in at a fake authorization server and a fake browser; returns both."""
    server = FakeAuthServer()
    browser = FakeBrowser(complete=complete_browser)
    monkeypatch.setattr(core_oauth, "_resolve_addrs", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(cli_oauth, "OAuthClient", server.client)
    monkeypatch.setattr(cli_oauth.webbrowser, "open", browser.open)
    # Poll the device grant fast.
    monkeypatch.setattr(core_oauth, "DEVICE_POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(core_oauth, "_MIN_DEVICE_POLL_S", 0.05)
    return server, browser


async def listener_is_closed(redirect_uri: str) -> bool:
    """Whether nothing listens at the loopback ``redirect_uri`` any more."""
    try:
        async with httpx.AsyncClient(timeout=1) as client:
            await client.get(redirect_uri)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # Windows retries a refused loopback connect for about two seconds, so a
        # closed port can surface as a timeout rather than a refusal.
        return True
    return False
