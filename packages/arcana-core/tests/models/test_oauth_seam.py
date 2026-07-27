"""The OAuth credential seam on the model side.

Covers the two new behaviours the adapters and gateway gained: per-request auth
injection (an API key or OAuth token resolved through a CredentialProvider, sent
as the right header per provider) and the bounded reactive 401→refresh→retry
decision. The static ``api_key`` path (no provider) is asserted unchanged.
"""

import pytest

from arcana.auth.provider import ApiKeyCredentialProvider, OAuthCredentialProvider
from arcana.models.adapters.anthropic import AnthropicAdapter
from arcana.models.adapters.custom_api import CustomAPIAdapter
from arcana.models.adapters.openai_compat import OpenAICompatAdapter
from arcana.models.errors import ModelAuthError, ModelBadRequestError
from arcana.models.gateway import DEFAULT_PROVIDERS, _build_credentials
from arcana.types.auth import AuthType, OAuthConfig
from arcana.types.model import ModelConnection, ModelProvider


class _FakeProvider:
    """A CredentialProvider double: hands out a fixed token, counts refreshes."""

    def __init__(self, token: str = "tok-123", *, refresh_ok: bool = True) -> None:
        self._token = token
        self._refresh_ok = refresh_ok
        self.reauth_calls = 0

    async def get_token(self) -> str:
        return self._token

    async def on_unauthorized(self) -> bool:
        self.reauth_calls += 1
        return self._refresh_ok


# ---------------------------------------------------------------------------
# Per-request auth headers
# ---------------------------------------------------------------------------


async def test_anthropic_oauth_sends_authorization_bearer():
    adapter = AnthropicAdapter(credentials=_FakeProvider("T"), auth_type=AuthType.OAUTH)
    assert await adapter._auth_headers() == {"Authorization": "Bearer T"}  # pyright: ignore[reportPrivateUsage]


async def test_anthropic_api_key_provider_sends_x_api_key():
    adapter = AnthropicAdapter(credentials=_FakeProvider("K"), auth_type=AuthType.API_KEY)
    assert await adapter._auth_headers() == {"x-api-key": "K"}  # pyright: ignore[reportPrivateUsage]


async def test_anthropic_legacy_path_injects_no_header():
    # No provider → the frozen client carries the key; no per-request override.
    adapter = AnthropicAdapter(api_key="frozen")
    assert await adapter._auth_headers() is None  # pyright: ignore[reportPrivateUsage]


async def test_openai_and_custom_send_bearer_or_none():
    oai = OpenAICompatAdapter("m", credentials=_FakeProvider("B"))
    assert await oai._auth_headers() == {"Authorization": "Bearer B"}  # pyright: ignore[reportPrivateUsage]
    assert await OpenAICompatAdapter("m")._auth_headers() is None  # pyright: ignore[reportPrivateUsage]

    cust = CustomAPIAdapter("m", base_url="https://x.example", credentials=_FakeProvider("C"))
    assert await cust._auth_headers() == {"Authorization": "Bearer C"}  # pyright: ignore[reportPrivateUsage]
    await cust.aclose()


# ---------------------------------------------------------------------------
# Reactive retry decision (bounded to one)
# ---------------------------------------------------------------------------


async def test_reauth_refreshes_once_on_first_auth_error():
    provider = _FakeProvider(refresh_ok=True)
    adapter = AnthropicAdapter(credentials=provider, auth_type=AuthType.OAUTH)
    exc = ModelAuthError("401")
    assert await adapter._reauth(exc, "m", attempt=0) is True  # pyright: ignore[reportPrivateUsage]
    assert provider.reauth_calls == 1


async def test_reauth_does_not_retry_on_second_attempt():
    provider = _FakeProvider(refresh_ok=True)
    adapter = AnthropicAdapter(credentials=provider, auth_type=AuthType.OAUTH)
    # attempt=1 is the retry itself — never refresh/retry again (bound of one).
    assert await adapter._reauth(ModelAuthError("401"), "m", attempt=1) is False  # pyright: ignore[reportPrivateUsage]
    assert provider.reauth_calls == 0


async def test_reauth_ignores_non_auth_errors():
    provider = _FakeProvider()
    adapter = AnthropicAdapter(credentials=provider, auth_type=AuthType.OAUTH)
    assert await adapter._reauth(ModelBadRequestError("bad"), "m", attempt=0) is False  # pyright: ignore[reportPrivateUsage]
    assert provider.reauth_calls == 0


async def test_reauth_returns_false_without_a_provider():
    adapter = AnthropicAdapter(api_key="frozen")  # legacy path, no provider
    assert await adapter._reauth(ModelAuthError("401"), "m", attempt=0) is False  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Gateway credential selection
# ---------------------------------------------------------------------------


def _entry(alias: str):
    entry = DEFAULT_PROVIDERS.get(alias)
    assert entry is not None
    return entry


def test_gateway_builds_oauth_provider_for_oauth_connection():
    conn = ModelConnection(
        name="c",
        provider=ModelProvider.ANTHROPIC,
        auth_type=AuthType.OAUTH,
        oauth_config=OAuthConfig(issuer="https://as.example.test"),
        credential_ref="c_oauth_token",
    )
    provider = _build_credentials(conn, _entry("anthropic"))
    assert isinstance(provider, OAuthCredentialProvider)


def test_gateway_builds_api_key_provider_for_api_key_connection():
    conn = ModelConnection(name="c", provider=ModelProvider.ANTHROPIC)  # default auth_type=api_key
    provider = _build_credentials(conn, _entry("anthropic"))
    assert isinstance(provider, ApiKeyCredentialProvider)


def test_gateway_builds_no_provider_for_keyless_provider():
    conn = ModelConnection(name="c", provider=ModelProvider.OLLAMA)
    assert _build_credentials(conn, _entry("ollama")) is None


def test_gateway_oauth_connection_missing_config_fails_closed():
    from arcana.models.errors import ModelNotConfiguredError

    conn = ModelConnection(name="c", provider=ModelProvider.ANTHROPIC, auth_type=AuthType.OAUTH)
    with pytest.raises(ModelNotConfiguredError):
        _build_credentials(conn, _entry("anthropic"))
