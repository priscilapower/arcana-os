"""Tests for the shared credential seam (``arcana.auth.provider``).

Covers the keyring token helpers, the never-refresh API-key provider, and the
OAuth provider's proactive + reactive refresh, rotation persistence, and
single-flight refresh under concurrency. All hermetic: an in-memory keyring and
an injected fake ``OAuthClient`` — no keychain, no network.
"""

import asyncio
from datetime import timedelta

import pytest

from arcana.auth.errors import AuthError
from arcana.auth.provider import (
    ApiKeyCredentialProvider,
    OAuthCredentialProvider,
    delete_token,
    load_token,
    save_token,
)
from arcana.types._utils import now_utc
from arcana.types.auth import OAuthConfig, OAuthToken

REF = "conn-123_oauth_token"
CONFIG = OAuthConfig(issuer="https://as.example.test", client_id="c1")


# ---------------------------------------------------------------------------
# Keyring token helpers
# ---------------------------------------------------------------------------


def test_token_helpers_round_trip(fake_keyring: dict[str, str]):
    token = OAuthToken(access_token="a", refresh_token="r", scopes=["read"])
    save_token(REF, token)
    assert load_token(REF) == token
    # Stored as JSON under the shared "arcana" service; never the raw string.
    assert "arcana/" + REF in fake_keyring
    delete_token(REF)
    assert load_token(REF) is None


def test_load_token_returns_none_for_corrupt_entry(fake_keyring: dict[str, str]):
    fake_keyring["arcana/" + REF] = "not-json"
    assert load_token(REF) is None


# ---------------------------------------------------------------------------
# ApiKeyCredentialProvider — never refreshes
# ---------------------------------------------------------------------------


async def test_api_key_provider_returns_key():
    provider = ApiKeyCredentialProvider(lambda: "sk-123")
    assert await provider.get_token() == "sk-123"


async def test_api_key_provider_fails_closed_when_no_key():
    provider = ApiKeyCredentialProvider(lambda: None)
    with pytest.raises(AuthError):
        await provider.get_token()


async def test_api_key_provider_never_refreshes():
    provider = ApiKeyCredentialProvider(lambda: "sk-123")
    assert await provider.on_unauthorized() is False


# ---------------------------------------------------------------------------
# OAuthCredentialProvider
# ---------------------------------------------------------------------------


class _FakeOAuthClient:
    """An injected OAuthClient whose ``refresh`` rotates the token and counts calls."""

    def __init__(self, counter: list[int]) -> None:
        self._counter = counter

    async def __aenter__(self) -> "_FakeOAuthClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def refresh(self, _config: OAuthConfig, token: OAuthToken) -> OAuthToken:
        self._counter.append(1)
        n = len(self._counter)
        return OAuthToken(
            access_token=f"access-{n}",
            refresh_token=f"refresh-{n}",
            expires_at=now_utc() + timedelta(hours=1),
        )


def _provider(counter: list[int]) -> OAuthCredentialProvider:
    return OAuthCredentialProvider(CONFIG, REF, client_factory=lambda: _FakeOAuthClient(counter))  # type: ignore[arg-type]


async def test_valid_token_is_returned_without_refresh(fake_keyring: dict[str, str]):
    save_token(REF, OAuthToken(access_token="live", expires_at=now_utc() + timedelta(hours=1)))
    counter: list[int] = []
    assert await _provider(counter).get_token() == "live"
    assert counter == []  # no refresh


async def test_expiring_token_is_refreshed_proactively(fake_keyring: dict[str, str]):
    # Within the default 60s skew → proactive refresh.
    save_token(REF, OAuthToken(access_token="stale", refresh_token="r", expires_at=now_utc() + timedelta(seconds=5)))
    counter: list[int] = []
    token = await _provider(counter).get_token()
    assert token == "access-1"
    assert counter == [1]
    # The rotated refresh token was persisted before the access token was handed out.
    assert load_token(REF).refresh_token == "refresh-1"


async def test_missing_token_fails_closed(fake_keyring: dict[str, str]):
    with pytest.raises(AuthError):
        await _provider([]).get_token()


async def test_on_unauthorized_refreshes_once_then_signals_retry(fake_keyring: dict[str, str]):
    save_token(REF, OAuthToken(access_token="old", refresh_token="r", expires_at=now_utc() + timedelta(hours=1)))
    counter: list[int] = []
    provider = _provider(counter)
    assert await provider.on_unauthorized() is True
    assert counter == [1]
    assert load_token(REF).access_token == "access-1"


async def test_on_unauthorized_without_refresh_token_does_not_retry(fake_keyring: dict[str, str]):
    save_token(REF, OAuthToken(access_token="old", expires_at=now_utc() + timedelta(hours=1)))
    counter: list[int] = []
    assert await _provider(counter).on_unauthorized() is False
    assert counter == []


async def test_concurrent_get_token_triggers_a_single_refresh(fake_keyring: dict[str, str]):
    save_token(REF, OAuthToken(access_token="stale", refresh_token="r", expires_at=now_utc() + timedelta(seconds=1)))
    counter: list[int] = []
    provider = _provider(counter)
    results = await asyncio.gather(*(provider.get_token() for _ in range(8)))
    # The per-connection lock + double-check collapse the stampede to one refresh.
    assert counter == [1]
    assert set(results) == {"access-1"}


# ---------------------------------------------------------------------------
# Re-auth hint in error messages — points the user at the right `login` command
# ---------------------------------------------------------------------------


async def test_not_authenticated_error_names_the_reauth_command(fake_keyring: dict[str, str]):
    provider = OAuthCredentialProvider(CONFIG, REF, reauth_hint="arcana providers login claude")
    with pytest.raises(AuthError, match="arcana providers login claude"):
        await provider.get_token()


async def test_refresh_failure_surfaces_the_reauth_command(fake_keyring: dict[str, str]):
    from arcana.auth.errors import TokenRefreshError

    class _FailingClient:
        async def __aenter__(self) -> "_FailingClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def refresh(self, _c: OAuthConfig, _t: OAuthToken) -> OAuthToken:
            raise TokenRefreshError("upstream rejected the refresh token")

    save_token(REF, OAuthToken(access_token="stale", refresh_token="r", expires_at=now_utc() + timedelta(seconds=1)))
    provider = OAuthCredentialProvider(
        CONFIG, REF, client_factory=lambda: _FailingClient(), reauth_hint="arcana mcp login notion"
    )
    with pytest.raises(TokenRefreshError, match="arcana mcp login notion"):
        await provider.get_token()
