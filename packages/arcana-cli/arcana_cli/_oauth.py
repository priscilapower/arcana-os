"""Interactive OAuth sign-in for the CLI.

The core :mod:`arcana.auth` module is deliberately UI-agnostic — it knows how to
talk to authorization servers but not how to open a browser or print a code.
This module supplies that missing half: it drives the loopback / device flows,
renders the user-facing prompts with Rich, and hands back the resulting
:class:`OAuthToken` plus the :class:`OAuthConfig` (with the resolved
``client_id`` filled in) for the caller to persist.

Everything network-facing goes through an injectable ``client_factory`` so tests
drive a fake authorization server without a real browser, socket, or network.
"""

import webbrowser
from collections.abc import Callable

import httpx
from rich.console import Console

from arcana.auth import (
    DeviceAuthResponse,
    OAuthClient,
    ProtectedResourceMetadata,
)
from arcana.types.auth import OAuthConfig, OAuthToken
from arcana_cli.ui.theme import dim, hl, ok

OAuthClientFactory = Callable[[], OAuthClient]

# RFC 9728 well-known path for Protected Resource Metadata, joined to the MCP
# server's origin when probing whether a target advertises OAuth.
_PRM_WELL_KNOWN = "/.well-known/oauth-protected-resource"


def _browser_opener(console: Console) -> Callable[[str], None]:
    def _open(url: str) -> None:
        console.print(dim("  Opening your browser to complete sign-in…"))
        console.print(dim(f"  If it doesn't open, visit:\n  {url}"))
        try:
            webbrowser.open(url)
        except Exception:
            # A headless host may have no browser; the printed URL is the fallback.
            pass

    return _open


def _device_notifier(console: Console) -> Callable[[DeviceAuthResponse], None]:
    def _notify(device: DeviceAuthResponse) -> None:
        target = device.verification_uri_complete or device.verification_uri
        console.print(f"\n  {hl('To sign in:')} open [bold]{device.verification_uri}[/]")
        console.print(f"  {hl('Enter code:')} [bold]{device.user_code}[/]")
        if device.verification_uri_complete:
            console.print(dim(f"  (or open {target} directly)"))
        console.print(dim("  Waiting for authorization…"))

    return _notify


async def sign_in(
    config: OAuthConfig,
    *,
    device: bool,
    console: Console,
    client_factory: OAuthClientFactory = OAuthClient,
) -> tuple[OAuthToken, OAuthConfig]:
    """Run the interactive OAuth flow and return ``(token, resolved_config)``.

    Discovers the authorization server, dynamically registers a client when none
    is pre-provisioned, then runs the loopback authorization-code flow (or the
    device grant when ``device`` is set). The returned config carries the
    resolved ``client_id`` so a later refresh has what it needs.
    """
    async with client_factory() as client:
        metadata = await client.discover_auth_server(config)
        client_id = config.client_id
        if not client_id:
            registration = await client.register_client(metadata, config.scopes)
            client_id = registration.client_id
        scopes = config.scopes or metadata.scopes_supported
        if device:
            token = await client.authorize_device(metadata, client_id, scopes, notify=_device_notifier(console))
        else:
            token = await client.authorize_code(metadata, client_id, scopes, open_browser=_browser_opener(console))

    console.print(ok("Signed in — token stored in the OS keyring."))
    resolved = config.model_copy(update={"client_id": client_id, "scopes": scopes})
    return token, resolved


async def probe_oauth(server_url: str, *, client_factory: OAuthClientFactory = OAuthClient) -> OAuthConfig | None:
    """Return an OAuth config if ``server_url`` advertises OAuth, else ``None``.

    Fetches the RFC 9728 Protected Resource Metadata from the server's
    well-known path. A reachable, valid document names the authorization
    server(s); the first becomes the issuer. Any failure (no metadata, not
    reachable, malformed) returns ``None`` — the caller falls back to the static
    path — so a plain/keyless server is never forced down the OAuth flow.
    """
    prm_url = _prm_url(server_url)
    if prm_url is None:
        return None
    try:
        async with client_factory() as client:
            prm = await client.discover_protected_resource(prm_url)
    except Exception:
        return None
    return _config_from_prm(prm)


def _config_from_prm(prm: ProtectedResourceMetadata) -> OAuthConfig | None:
    if not prm.authorization_servers:
        return None
    return OAuthConfig(issuer=prm.authorization_servers[0])


def _prm_url(server_url: str) -> str | None:
    """The well-known PRM URL for a server origin, or ``None`` if not parseable."""
    parts = httpx.URL(server_url)
    if not parts.host:
        return None
    origin = httpx.URL(scheme=parts.scheme or "https", host=parts.host, port=parts.port)
    return str(origin.join(_PRM_WELL_KNOWN))
