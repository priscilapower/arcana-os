# Auth

Unified credentials for model providers and MCP servers. A connection
authenticates by either a static **API key** or **OAuth 2.1** (authorization-code
+ PKCE, with a device-grant fallback), selected by an `auth_type` discriminator.
Both kinds flow through one shared `CredentialProvider`, and OAuth tokens live in
the OS keyring — never in `models.json` / `mcps.json`.

## Credential provider

The seam both adapter families depend on: it hands an adapter a valid credential
and knows how to react to a `401`. Adapters never touch the keyring or refresh
logic directly.

::: arcana.auth.provider.CredentialProvider

::: arcana.auth.provider.OAuthCredentialProvider

::: arcana.auth.provider.ApiKeyCredentialProvider

## Token storage

Keyring helpers — the one place an `OAuthToken` is (de)serialized to the OS
keychain. Config on disk holds only the reference.

::: arcana.auth.provider.load_token

::: arcana.auth.provider.save_token

::: arcana.auth.provider.delete_token

## OAuth flows

The only component that talks to authorization servers: metadata discovery
(RFC 9728 → 8414), dynamic client registration (RFC 7591), authorization-code +
PKCE over a loopback listener, and the device-authorization grant (RFC 8628).
Every response is parsed through Pydantic; discovery is SSRF-guarded.

::: arcana.auth.oauth.OAuthClient

::: arcana.auth.oauth.assert_safe_issuer

## Errors

::: arcana.auth.errors.AuthError

::: arcana.auth.errors.TokenRefreshError

::: arcana.auth.errors.DiscoveryError

::: arcana.auth.errors.AuthorizationError
