"""OAuth on the MCP side: credential selection, HTTP transport, and gating.

The static SSE bearer path is covered in ``test_mcp_adapter`` and the security
tier; here we pin the new OAuth wiring — how a config's ``auth_type`` maps to a
credential provider, and that an OAuth server resolves its bearer through it.
"""

import keyring
import pytest

from arcana.auth.provider import ApiKeyCredentialProvider, OAuthCredentialProvider, save_token
from arcana.tools.adapters.mcp import MCPToolAdapter, build_mcp_credentials
from arcana.types.auth import AuthType, OAuthConfig, OAuthToken
from arcana.types.tool import MCPServerConfig, MCPTransport


def _oauth_cfg(**kw: object) -> MCPServerConfig:
    return MCPServerConfig(
        name="notion",
        server_url="https://mcp.notion.com/mcp",
        transport=MCPTransport.HTTP,
        auth_type=AuthType.OAUTH,
        oauth_config=OAuthConfig(issuer="https://as.example.test"),
        auth_key_ref="mcp_notion_auth",
        **kw,  # type: ignore[arg-type]
    )


def test_build_credentials_oauth():
    provider = build_mcp_credentials(_oauth_cfg())
    assert isinstance(provider, OAuthCredentialProvider)


def test_build_credentials_oauth_without_ref_is_none():
    cfg = MCPServerConfig(name="s", transport=MCPTransport.HTTP, auth_type=AuthType.OAUTH)
    assert build_mcp_credentials(cfg) is None


def test_build_credentials_static_bearer():
    cfg = MCPServerConfig(name="s", server_url="https://s/mcp", transport=MCPTransport.SSE, auth_key_ref="ref")
    assert isinstance(build_mcp_credentials(cfg), ApiKeyCredentialProvider)


def test_build_credentials_none_without_ref():
    cfg = MCPServerConfig(name="s", server_url="https://s/mcp", transport=MCPTransport.SSE)
    assert build_mcp_credentials(cfg) is None


async def test_oauth_auth_header_resolves_the_keyring_token(monkeypatch: pytest.MonkeyPatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, r, v: store.__setitem__(f"{s}/{r}", v))
    monkeypatch.setattr(keyring, "get_password", lambda s, r: store.get(f"{s}/{r}"))
    # A live (non-expired) token in the keyring resolves into an Authorization header.
    from datetime import timedelta

    from arcana.types._utils import now_utc

    save_token("mcp_notion_auth", OAuthToken(access_token="live-token", expires_at=now_utc() + timedelta(hours=1)))
    adapter = MCPToolAdapter(_oauth_cfg())
    headers = await adapter._auth_headers()  # pyright: ignore[reportPrivateUsage]
    assert headers == {"Authorization": "Bearer live-token"}


async def test_oauth_server_with_incomplete_config_fails_closed():
    """A server marked oauth but missing its config must never send an
    unauthenticated request — it fails closed with an AuthError."""
    from arcana.auth.errors import AuthError

    # auth_type=oauth but no oauth_config / auth_key_ref → build_mcp_credentials
    # yields no provider; _auth_headers must refuse rather than send no header.
    cfg = MCPServerConfig(
        name="broken", server_url="https://s/mcp", transport=MCPTransport.HTTP, auth_type=AuthType.OAUTH
    )
    adapter = MCPToolAdapter(cfg)
    with pytest.raises(AuthError):
        await adapter._auth_headers()  # pyright: ignore[reportPrivateUsage]
