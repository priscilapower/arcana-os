"""Unit + integration tests for the OAuth 2.1 flows (``arcana.auth.oauth``).

Hermetic throughout: the authorization server is an ``httpx.MockTransport`` fake,
the SSRF resolver is patched to a public IP, and the loopback flow drives a real
ephemeral ``127.0.0.1`` listener with an in-process client — no browser, no
outside network.
"""

import asyncio
import base64
import hashlib

import httpx
import pytest

from arcana.auth.errors import AuthorizationError, DiscoveryError, TokenRefreshError
from arcana.auth.oauth import (
    PKCE,
    OAuthClient,
    _build_authorize_url,
    _well_known_metadata_url,
    loopback_listener,
)
from arcana.types.auth import OAuthConfig, OAuthToken
from tests.auth.conftest import ISSUER, TOKEN_ENDPOINT, FakeAuthServer, open_browser_capturing

# ---------------------------------------------------------------------------
# PKCE + state
# ---------------------------------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier():
    pkce = PKCE.generate()
    expected = base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest()).rstrip(b"=").decode()
    assert pkce.challenge == expected
    # base64url, no padding, and a non-trivial state nonce.
    assert "=" not in pkce.verifier and "=" not in pkce.challenge
    assert pkce.state and pkce.state != pkce.verifier


def test_each_pkce_is_unique():
    a, b = PKCE.generate(), PKCE.generate()
    assert a.verifier != b.verifier
    assert a.state != b.state


def test_authorize_url_carries_pkce_state_and_exact_redirect():
    url = _build_authorize_url(
        "https://as.example.test/authorize",
        client_id="c1",
        redirect_uri="http://127.0.0.1:5555/callback",
        scopes=["read", "write"],
        state="the-state",
        challenge="the-challenge",
    )
    q = httpx.URL(url).params
    assert q["code_challenge"] == "the-challenge"
    assert q["code_challenge_method"] == "S256"
    assert q["state"] == "the-state"
    assert q["redirect_uri"] == "http://127.0.0.1:5555/callback"
    assert q["response_type"] == "code"


def test_well_known_metadata_url_inserts_the_path():
    assert _well_known_metadata_url("https://as.example.test") == (
        "https://as.example.test/.well-known/oauth-authorization-server"
    )


# ---------------------------------------------------------------------------
# OAuthToken semantics
# ---------------------------------------------------------------------------


def test_token_json_round_trip():
    from datetime import UTC, datetime

    token = OAuthToken(
        access_token="a", refresh_token="r", expires_at=datetime(2030, 1, 1, tzinfo=UTC), scopes=["read"]
    )
    assert OAuthToken.model_validate_json(token.model_dump_json()) == token


def test_token_with_no_expiry_never_reports_expired():
    from datetime import timedelta

    assert OAuthToken(access_token="a").is_expired(timedelta(seconds=60)) is False


def test_expires_at_computed_from_expires_in(fake_as: FakeAuthServer):
    # An access token with a 3600s lifetime is not expired now, but is within a
    # skew window larger than its lifetime.
    from datetime import timedelta

    from arcana.auth.oauth import _TokenResponse

    tok = _TokenResponse(access_token="a", expires_in=3600).to_token(fallback_scopes=[])
    assert tok.is_expired(timedelta(seconds=1)) is False
    assert tok.is_expired(timedelta(seconds=7200)) is True


# ---------------------------------------------------------------------------
# Discovery / DCR / exchange / refresh against the fake AS
# ---------------------------------------------------------------------------


async def test_discovery_parses_metadata(fake_as: FakeAuthServer):
    async with fake_as.client() as client:
        meta = await client.discover_auth_server(OAuthConfig(issuer=ISSUER))
    assert meta.token_endpoint == TOKEN_ENDPOINT
    assert "read" in meta.scopes_supported


async def test_dynamic_client_registration(fake_as: FakeAuthServer):
    async with fake_as.client() as client:
        meta = await client.discover_auth_server(OAuthConfig(issuer=ISSUER))
        reg = await client.register_client(meta, ["read"])
    assert reg.client_id == "dyn-client-123"


async def test_refresh_sends_refresh_grant_and_persists_rotation(fake_as: FakeAuthServer):
    fake_as.token_body = {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}
    async with fake_as.client() as client:
        new = await client.refresh(
            OAuthConfig(issuer=ISSUER, client_id="c1"), OAuthToken(access_token="old", refresh_token="refresh-1")
        )
    assert new.access_token == "access-2"
    assert new.refresh_token == "refresh-2"  # rotated
    sent = fake_as.token_calls[-1]
    assert sent["grant_type"] == ["refresh_token"]
    assert sent["refresh_token"] == ["refresh-1"]


async def test_refresh_carries_forward_refresh_token_when_server_omits_it(fake_as: FakeAuthServer):
    fake_as.token_body = {"access_token": "access-2", "expires_in": 3600}  # no refresh_token
    async with fake_as.client() as client:
        new = await client.refresh(OAuthConfig(issuer=ISSUER), OAuthToken(access_token="old", refresh_token="keep-me"))
    assert new.refresh_token == "keep-me"


async def test_refresh_without_a_refresh_token_fails_closed(fake_as: FakeAuthServer):
    async with fake_as.client() as client:
        with pytest.raises(TokenRefreshError):
            await client.refresh(OAuthConfig(issuer=ISSUER), OAuthToken(access_token="a"))


async def test_refresh_error_from_server_raises_token_refresh_error(fake_as: FakeAuthServer):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=fake_as.metadata)
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = OAuthClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    async with client:
        with pytest.raises(TokenRefreshError):
            await client.refresh(OAuthConfig(issuer=ISSUER), OAuthToken(access_token="a", refresh_token="r"))


# ---------------------------------------------------------------------------
# Loopback authorization-code flow (real ephemeral listener)
# ---------------------------------------------------------------------------


async def _drive_callback(redirect_uri: str, query: str) -> None:
    """Fire a single GET at the loopback listener, as the browser redirect would."""
    url = httpx.URL(redirect_uri).copy_with(query=query.encode())
    async with httpx.AsyncClient() as c:
        await c.get(str(url))


async def test_loopback_listener_returns_callback_params():
    async with loopback_listener() as cb:
        waiter = asyncio.ensure_future(cb.wait(timeout=5))
        await _drive_callback(cb.redirect_uri, "code=the-code&state=abc")
        params = await waiter
    assert params == {"code": "the-code", "state": "abc"}


async def test_authorize_code_end_to_end(fake_as: FakeAuthServer):
    urls: list[str] = []

    async def _client_and_flow() -> OAuthToken:
        async with fake_as.client() as client:
            meta = await client.discover_auth_server(OAuthConfig(issuer=ISSUER))

            async def _authorize() -> OAuthToken:
                return await client.authorize_code(meta, "c1", ["read"], open_browser=open_browser_capturing(urls))

            task = asyncio.ensure_future(_authorize())
            # The browser opener recorded the authorize URL; echo its state back.
            await asyncio.sleep(0.05)
            state = httpx.URL(urls[0]).params["state"]
            redirect = httpx.URL(urls[0]).params["redirect_uri"]
            await _drive_callback(redirect, f"code=xyz&state={state}")
            return await task

    token = await _client_and_flow()
    assert token.access_token == "access-1"
    # The exchange sent the authorization_code grant with our code + verifier.
    exchange = fake_as.token_calls[-1]
    assert exchange["grant_type"] == ["authorization_code"]
    assert exchange["code"] == ["xyz"]
    assert "code_verifier" in exchange


async def test_authorize_code_rejects_state_mismatch(fake_as: FakeAuthServer):
    urls: list[str] = []
    async with fake_as.client() as client:
        meta = await client.discover_auth_server(OAuthConfig(issuer=ISSUER))

        async def _authorize() -> OAuthToken:
            return await client.authorize_code(meta, "c1", ["read"], open_browser=open_browser_capturing(urls))

        task = asyncio.ensure_future(_authorize())
        await asyncio.sleep(0.05)
        redirect = httpx.URL(urls[0]).params["redirect_uri"]
        await _drive_callback(redirect, "code=xyz&state=WRONG")
        with pytest.raises(AuthorizationError, match="state mismatch"):
            await task


async def test_authorize_code_rejects_error_callback(fake_as: FakeAuthServer):
    urls: list[str] = []
    async with fake_as.client() as client:
        meta = await client.discover_auth_server(OAuthConfig(issuer=ISSUER))

        async def _authorize() -> OAuthToken:
            return await client.authorize_code(meta, "c1", ["read"], open_browser=open_browser_capturing(urls))

        task = asyncio.ensure_future(_authorize())
        await asyncio.sleep(0.05)
        redirect = httpx.URL(urls[0]).params["redirect_uri"]
        await _drive_callback(redirect, "error=access_denied")
        with pytest.raises(AuthorizationError, match="denied"):
            await task


# ---------------------------------------------------------------------------
# Device-authorization grant
# ---------------------------------------------------------------------------


async def test_device_grant_polls_to_success(fake_as: FakeAuthServer):
    fake_as.device_pending_polls = 2  # two 'pending' polls, then success
    seen: list[str] = []

    async with fake_as.client() as client:
        meta = await client.discover_auth_server(OAuthConfig(issuer=ISSUER))
        token = await client.authorize_device(meta, "c1", ["read"], notify=lambda d: seen.append(d.user_code))
    assert token.access_token == "access-1"
    assert seen == ["WXYZ-1234"]  # the user code was surfaced once
    assert fake_as.device_poll_count == 3  # 2 pending + 1 success


async def test_discovery_rejects_missing_issuer_and_metadata():
    async with OAuthClient(http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))) as c:
        with pytest.raises(DiscoveryError):
            await c.discover_auth_server(OAuthConfig())
