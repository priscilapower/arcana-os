"""Interactive OAuth sign-in for the CLI.

The core :mod:`arcana.auth` module is deliberately UI-agnostic — it knows how to
talk to authorization servers but not how to open a browser or print a code.
This module supplies that missing half: it drives the loopback / device flows,
shows the user what to do through the :class:`~arcana_cli.ui.renderer.Renderer`
it is handed (a wait dialog in the session, printed lines at a terminal), and
hands back the resulting :class:`OAuthToken` plus the :class:`OAuthConfig`
(with the resolved ``client_id`` filled in) for the caller to persist.

The flows run on the caller's event loop: the loopback listener is an asyncio
server bound to ``127.0.0.1`` and the device grant polls with ``asyncio.sleep``,
so cancelling the sign-in (Esc on the wait dialog, Ctrl+C) closes the listener
and stops the polling. Nothing here writes to the terminal directly.

Everything network-facing goes through an injectable ``client_factory`` so tests
drive a fake authorization server without a real browser, socket, or network.
"""

import webbrowser
from collections.abc import Callable

import httpx
import typer
from rich.console import Group, RenderableType
from rich.markup import escape
from rich.text import Text

from arcana.auth import (
    DeviceAuthResponse,
    OAuthClient,
    ProtectedResourceMetadata,
)
from arcana.types.auth import OAuthConfig, OAuthToken
from arcana_cli.ui.renderer import Renderer, WaitHandle, fail
from arcana_cli.ui.theme import dim, hl, ok

OAuthClientFactory = Callable[[], OAuthClient]

# RFC 9728 well-known path for Protected Resource Metadata, joined to the MCP
# server's origin when probing whether a target advertises OAuth.
_PRM_WELL_KNOWN = "/.well-known/oauth-protected-resource"

#: The title of the wait shown while the user signs in.
SIGN_IN_TITLE = "Sign in"
#: The in-progress line of that wait.
WAITING_FOR_AUTHORIZATION = "Waiting for authorization…"


def _browser_instructions(url: str) -> RenderableType:
    return Group(
        Text.from_markup(dim("  Opening your browser to complete sign-in…")),
        Text.from_markup(dim(f"  If it doesn't open, visit:\n  {escape(url)}")),
    )


def _device_instructions(device: DeviceAuthResponse) -> RenderableType:
    lines = [
        Text.from_markup(f"\n  {hl('To sign in:')} open [bold]{escape(device.verification_uri)}[/]"),
        Text.from_markup(f"  {hl('Enter code:')} [bold]{escape(device.user_code)}[/]"),
    ]
    if device.verification_uri_complete:
        lines.append(Text.from_markup(dim(f"  (or open {escape(device.verification_uri_complete)} directly)")))
    return Group(*lines)


def _browser_opener(wait: WaitHandle) -> Callable[[str], None]:
    def _open(url: str) -> None:
        wait.show(_browser_instructions(url))
        try:
            webbrowser.open(url)
        except Exception:
            # A headless host may have no browser; the URL shown is the fallback.
            pass

    return _open


def _device_notifier(wait: WaitHandle) -> Callable[[DeviceAuthResponse], None]:
    def _notify(device: DeviceAuthResponse) -> None:
        wait.show(_device_instructions(device))

    return _notify


async def sign_in(
    r: Renderer,
    config: OAuthConfig,
    *,
    device: bool,
    client_factory: OAuthClientFactory | None = None,
) -> tuple[OAuthToken, OAuthConfig]:
    """Run the interactive OAuth flow and return ``(token, resolved_config)``.

    Discovers the authorization server, dynamically registers a client when none
    is pre-provisioned, then runs the loopback authorization-code flow (or the
    device grant when ``device`` is set) inside a :meth:`Renderer.waiting` block
    showing the URL or code. The returned config carries the resolved
    ``client_id`` so a later refresh has what it needs. The token is returned,
    never shown. A wait the user calls off raises :class:`typer.Abort`.
    ``client_factory`` defaults to :class:`OAuthClient`.
    """
    async with (client_factory or OAuthClient)() as client:
        metadata = await client.discover_auth_server(config)
        client_id = config.client_id
        if not client_id:
            registration = await client.register_client(metadata, config.scopes)
            client_id = registration.client_id
        scopes = config.scopes or metadata.scopes_supported
        async with r.waiting(WAITING_FOR_AUTHORIZATION, title=SIGN_IN_TITLE) as wait:
            if device:
                token = await client.authorize_device(metadata, client_id, scopes, notify=_device_notifier(wait))
            else:
                token = await client.authorize_code(metadata, client_id, scopes, open_browser=_browser_opener(wait))

    r.note(ok("Signed in — token stored in the OS keyring."))
    resolved = config.model_copy(update={"client_id": client_id, "scopes": scopes})
    return token, resolved


async def sign_in_or_exit(
    r: Renderer, config: OAuthConfig, *, device: bool, code: int
) -> tuple[OAuthToken, OAuthConfig]:
    """:func:`sign_in`, turning a failed sign-in into :func:`~arcana_cli.ui.renderer.fail` with ``code``.

    A sign-in the user called off still raises :class:`typer.Abort`.
    """
    try:
        return await sign_in(r, config, device=device)
    except typer.Abort:
        raise
    except Exception as exc:
        fail(r, f"OAuth sign-in failed: {exc}", code=code)


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
