"""OAuth 2.1 flows — the only module that talks to authorization servers.

Implements the standards path the MCP authorization spec mandates and OAuth 2.1
blesses for native apps:

* **Discovery** — Protected Resource Metadata (RFC 9728) → Authorization Server
  Metadata (RFC 8414).
* **Registration** — Dynamic Client Registration (RFC 7591) when no ``client_id``
  is pre-provisioned.
* **Authorization** — authorization-code + **PKCE (S256)** via a ``127.0.0.1``
  loopback listener, with the **device-authorization grant** (RFC 8628) as the
  headless fallback.
* **Tokens** — code exchange and refresh over ``httpx.AsyncClient``.

Every network response is parsed through a Pydantic model — crafted server
metadata is data, never trusted raw. Discovery is SSRF-guarded: issuers must be
``https`` and must not resolve to internal/link-local ranges. Failures raise the
typed errors in :mod:`arcana.auth.errors`; nothing here logs or returns a token
in the clear.
"""

import asyncio
import base64
import hashlib
import ipaddress
import secrets
import socket
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ValidationError

from arcana.auth.config import DEVICE_POLL_INTERVAL_S, DEVICE_TIMEOUT_S, HTTP_TIMEOUT_S, LISTENER_TIMEOUT_S
from arcana.auth.errors import AuthError, AuthorizationError, DiscoveryError, TokenRefreshError
from arcana.types._utils import now_utc
from arcana.types.auth import OAuthConfig, OAuthToken

# The device-code grant's wire identifier (RFC 8628 §3.4).
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Loopback is the only non-https host the flows ever touch — the local callback
# listener, never an issuer.
_LOOPBACK_HOST = "127.0.0.1"

# A hostile device endpoint could return a near-zero poll interval to hammer the
# token endpoint; floor it.
_MIN_DEVICE_POLL_S = 1.0

_M = TypeVar("_M", bound=BaseModel)


# ---------------------------------------------------------------------------
# Wire response models — every server response is parsed through one of these.
# ---------------------------------------------------------------------------


class ProtectedResourceMetadata(BaseModel):
    """RFC 9728 — the document an MCP resource returns (or points to) on a 401."""

    resource: str | None = None
    authorization_servers: list[str] = []


class AuthServerMetadata(BaseModel):
    """RFC 8414 — an authorization server's advertised endpoints."""

    issuer: str
    authorization_endpoint: str | None = None
    token_endpoint: str
    registration_endpoint: str | None = None
    device_authorization_endpoint: str | None = None
    scopes_supported: list[str] = []
    grant_types_supported: list[str] = []
    code_challenge_methods_supported: list[str] = []


class ClientRegistration(BaseModel):
    """RFC 7591 — the dynamic client registration response."""

    client_id: str
    client_secret: str | None = None


class DeviceAuthResponse(BaseModel):
    """RFC 8628 §3.2 — the device-authorization response shown to the user."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int = 900
    interval: int = 5


class _TokenResponse(BaseModel):
    """The token endpoint's success body (RFC 6749 §5.1)."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: str | None = None

    def to_token(self, *, fallback_refresh: str | None = None, fallback_scopes: list[str]) -> OAuthToken:
        """Materialize an :class:`OAuthToken`, computing absolute ``expires_at``.

        ``fallback_refresh`` preserves a rotating refresh token across a refresh
        that omits a new one; ``fallback_scopes`` fills in the granted scopes
        when the server echoes none.
        """
        expires_at = now_utc() + timedelta(seconds=self.expires_in) if self.expires_in is not None else None
        scopes = self.scope.split() if self.scope else list(fallback_scopes)
        return OAuthToken(
            access_token=self.access_token,
            refresh_token=self.refresh_token or fallback_refresh,
            token_type=self.token_type or "Bearer",
            expires_at=expires_at,
            scopes=scopes,
        )


# ---------------------------------------------------------------------------
# PKCE + state
# ---------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    """Base64url without padding — the encoding PKCE and ``state`` use."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class PKCE(BaseModel):
    """A PKCE verifier/challenge pair (S256) plus a CSRF ``state`` nonce."""

    verifier: str
    challenge: str
    state: str

    @classmethod
    def generate(cls) -> "PKCE":
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return cls(verifier=verifier, challenge=challenge, state=_b64url(secrets.token_bytes(16)))


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def _resolve_addrs(host: str) -> list[str]:
    """Resolve ``host`` to its IP addresses (module-level so tests can patch)."""
    infos = socket.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def assert_safe_issuer(url: str) -> None:
    """Reject a discovery URL that is not ``https`` or resolves to a private range.

    The SSRF guard on every issuer / metadata / PRM URL: an ``http`` issuer, or
    one pointing at loopback, RFC 1918, link-local (``169.254.0.0/16``), or other
    reserved space, is refused before any request is made. Raises
    :class:`DiscoveryError`.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DiscoveryError(f"issuer must use https, got {parts.scheme or '(none)'}: {url!r}")
    host = parts.hostname
    if not host:
        raise DiscoveryError(f"issuer has no host: {url!r}")
    # An IP literal is checked directly (no DNS); a name is resolved and every
    # address it maps to must clear the guard.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise DiscoveryError(f"issuer resolves to a blocked address: {host}")
        return
    try:
        addrs = _resolve_addrs(host)
    except OSError as exc:
        raise DiscoveryError(f"could not resolve issuer host {host!r}: {exc}") from exc
    for addr in addrs:
        if _is_blocked_ip(ipaddress.ip_address(addr)):
            raise DiscoveryError(f"issuer host {host!r} resolves to a blocked address: {addr}")


# ---------------------------------------------------------------------------
# Loopback callback listener
# ---------------------------------------------------------------------------


class LoopbackCallback:
    """A one-shot ``127.0.0.1`` HTTP listener for the authorization redirect.

    Binds an ephemeral port, accepts exactly one callback, parses its query
    parameters, and returns a small confirmation page to the browser. Never
    binds a routable interface; times out fast if the callback never arrives.
    """

    def __init__(self, server: asyncio.AbstractServer, port: int, future: "asyncio.Future[dict[str, str]]") -> None:
        self._server = server
        self._future = future
        self.redirect_uri = f"http://{_LOOPBACK_HOST}:{port}/callback"

    async def wait(self, timeout: float) -> dict[str, str]:
        """Block for the callback's query params, or raise on timeout."""
        try:
            return await asyncio.wait_for(self._future, timeout=timeout)
        except TimeoutError as exc:
            raise AuthorizationError("timed out waiting for the authorization callback") from exc


@asynccontextmanager
async def loopback_listener() -> AsyncGenerator[LoopbackCallback, None]:
    """Open a one-shot loopback listener, tearing it down on exit."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, str]] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            params = _parse_callback_query(request_line.decode("latin-1", "ignore"))
            body = b"Arcana: sign-in complete. You can close this tab."
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
            if not future.done():
                future.set_result(params)
        except Exception:
            if not future.done():
                future.set_result({})
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, _LOOPBACK_HOST, 0)
    port = server.sockets[0].getsockname()[1]
    callback = LoopbackCallback(server, port, future)
    try:
        yield callback
    finally:
        server.close()
        await server.wait_closed()


def _parse_callback_query(request_line: str) -> dict[str, str]:
    """Pull the query params out of a raw ``GET /callback?… HTTP/1.1`` line."""
    parts = request_line.split(" ")
    if len(parts) < 2:
        return {}
    query = urlsplit(parts[1]).query
    return dict(parse_qsl(query))


# ---------------------------------------------------------------------------
# Interactive hooks (kept UI-agnostic — the CLI supplies these)
# ---------------------------------------------------------------------------

#: Opens the authorization URL in the user's browser. The CLI wires this to
#: ``webbrowser.open`` and prints the URL as a manual fallback.
BrowserOpener = Callable[[str], None]

#: Surfaces the device-grant ``user_code`` + verification URL to the user.
DeviceNotifier = Callable[[DeviceAuthResponse], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# OAuthClient
# ---------------------------------------------------------------------------


class OAuthClient:
    """Runs the network side of every OAuth flow over one ``httpx.AsyncClient``.

    Construct with an injected client in tests (a ``MockTransport`` fake
    authorization server); left unset, it owns a client with a bounded timeout
    and redirects disabled (an open redirect must not smuggle a request to an
    internal host).
    """

    def __init__(self, *, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, follow_redirects=False)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "OAuthClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover_protected_resource(self, url: str) -> ProtectedResourceMetadata:
        """Fetch RFC 9728 Protected Resource Metadata (SSRF-guarded)."""
        assert_safe_issuer(url)
        return await self._get_model(url, ProtectedResourceMetadata, DiscoveryError)

    async def discover_auth_server(self, config: OAuthConfig) -> AuthServerMetadata:
        """Resolve an authorization server's metadata for ``config``.

        Uses ``metadata_url`` verbatim when set; otherwise derives the RFC 8414
        well-known path from ``issuer``. The result is SSRF-guarded and parsed.
        """
        if config.metadata_url:
            url = config.metadata_url
        elif config.issuer:
            url = _well_known_metadata_url(config.issuer)
        else:
            raise DiscoveryError("OAuth config has neither issuer nor metadata_url")
        assert_safe_issuer(url)
        return await self._get_model(url, AuthServerMetadata, DiscoveryError)

    async def register_client(self, metadata: AuthServerMetadata, scopes: list[str]) -> ClientRegistration:
        """Dynamic Client Registration (RFC 7591) as a public native-app client."""
        if not metadata.registration_endpoint:
            raise DiscoveryError("authorization server does not advertise a registration endpoint")
        assert_safe_issuer(metadata.registration_endpoint)
        payload = {
            "client_name": "Arcana OS",
            "token_endpoint_auth_method": "none",  # public client — PKCE, no secret
            "grant_types": ["authorization_code", _DEVICE_GRANT, "refresh_token"],
            "response_types": ["code"],
        }
        if scopes:
            payload["scope"] = " ".join(scopes)
        try:
            resp = await self._http.post(metadata.registration_endpoint, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DiscoveryError(f"dynamic client registration failed: {type(exc).__name__}") from exc
        return _parse_model(resp, ClientRegistration, DiscoveryError)

    # ------------------------------------------------------------------
    # Authorization-code + PKCE (loopback)
    # ------------------------------------------------------------------

    async def authorize_code(
        self,
        metadata: AuthServerMetadata,
        client_id: str,
        scopes: list[str],
        open_browser: BrowserOpener,
    ) -> OAuthToken:
        """Run the interactive authorization-code + PKCE flow over loopback."""
        if not metadata.authorization_endpoint:
            raise AuthorizationError("authorization server does not advertise an authorization endpoint")
        # From the metadata body — guarded so the browser is never sent to an
        # internal/link-local or non-https authorize URL.
        assert_safe_issuer(metadata.authorization_endpoint)
        pkce = PKCE.generate()
        async with loopback_listener() as callback:
            auth_url = _build_authorize_url(
                metadata.authorization_endpoint,
                client_id=client_id,
                redirect_uri=callback.redirect_uri,
                scopes=scopes,
                state=pkce.state,
                challenge=pkce.challenge,
            )
            open_browser(auth_url)
            params = await callback.wait(LISTENER_TIMEOUT_S)

        if params.get("error"):
            raise AuthorizationError(f"authorization denied: {params['error']}")
        if params.get("state") != pkce.state:
            raise AuthorizationError("authorization callback state mismatch — request rejected")
        code = params.get("code")
        if not code:
            raise AuthorizationError("authorization callback carried no code")

        return await self._exchange_code(
            metadata, client_id=client_id, code=code, verifier=pkce.verifier, redirect_uri=callback.redirect_uri
        )

    async def _exchange_code(
        self, metadata: AuthServerMetadata, *, client_id: str, code: str, verifier: str, redirect_uri: str
    ) -> OAuthToken:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }
        return await self._token_request(metadata.token_endpoint, data, fallback_scopes=[], error=AuthorizationError)

    # ------------------------------------------------------------------
    # Device-authorization grant (RFC 8628)
    # ------------------------------------------------------------------

    async def authorize_device(
        self,
        metadata: AuthServerMetadata,
        client_id: str,
        scopes: list[str],
        notify: DeviceNotifier,
    ) -> OAuthToken:
        """Run the device-authorization grant: request a code, show it, poll."""
        if not metadata.device_authorization_endpoint:
            raise AuthorizationError("authorization server does not advertise a device endpoint")
        assert_safe_issuer(metadata.device_authorization_endpoint)
        data = {"client_id": client_id}
        if scopes:
            data["scope"] = " ".join(scopes)
        try:
            resp = await self._http.post(metadata.device_authorization_endpoint, data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthorizationError(f"device authorization request failed: {type(exc).__name__}") from exc
        device = _parse_model(resp, DeviceAuthResponse, AuthorizationError)

        result = notify(device)
        if asyncio.iscoroutine(result):
            await result

        return await self._poll_device_token(metadata, client_id, device)

    async def _poll_device_token(
        self, metadata: AuthServerMetadata, client_id: str, device: DeviceAuthResponse
    ) -> OAuthToken:
        # The token endpoint comes from the metadata response body — attacker-
        # influenceable — so it is SSRF-guarded before any secret is POSTed to it,
        # the same as the discovery URL (RFC 8414 allows it on a different host).
        assert_safe_issuer(metadata.token_endpoint)
        interval = max(float(device.interval or DEVICE_POLL_INTERVAL_S), _MIN_DEVICE_POLL_S)
        deadline = now_utc() + timedelta(seconds=min(device.expires_in, int(DEVICE_TIMEOUT_S)))
        data = {"grant_type": _DEVICE_GRANT, "device_code": device.device_code, "client_id": client_id}
        while now_utc() < deadline:
            await asyncio.sleep(interval)
            resp = await self._http.post(metadata.token_endpoint, data=data)
            if resp.is_success:
                parsed = _parse_model(resp, _TokenResponse, AuthorizationError)
                return parsed.to_token(fallback_scopes=[])
            error = _token_error_code(resp)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5.0
                continue
            raise AuthorizationError(f"device authorization failed: {error}")
        raise AuthorizationError("device authorization timed out before the user approved it")

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh(self, config: OAuthConfig, token: OAuthToken) -> OAuthToken:
        """Exchange a refresh token for a fresh access token, fail-closed.

        Re-discovers the token endpoint from ``config`` (SSRF-guarded) and posts
        the refresh grant. A rotated refresh token in the response is persisted
        by the caller; if the server omits one, the existing refresh token is
        carried forward. Any failure raises :class:`TokenRefreshError`.
        """
        if not token.refresh_token:
            raise TokenRefreshError("no refresh token available — re-authentication required")
        metadata = await self.discover_auth_server(config)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        }
        if config.client_id:
            data["client_id"] = config.client_id
        if config.scopes:
            data["scope"] = " ".join(config.scopes)
        return await self._token_request(
            metadata.token_endpoint,
            data,
            fallback_refresh=token.refresh_token,
            fallback_scopes=token.scopes,
            error=TokenRefreshError,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _token_request(
        self,
        token_endpoint: str,
        data: dict[str, str],
        *,
        fallback_refresh: str | None = None,
        fallback_scopes: list[str],
        error: type[AuthError],
    ) -> OAuthToken:
        # The token endpoint is taken from the (attacker-influenceable) metadata
        # body, so it is SSRF-guarded before the auth code / PKCE verifier /
        # refresh token is POSTed to it — never a fall-through to an internal host.
        try:
            assert_safe_issuer(token_endpoint)
        except DiscoveryError as exc:
            raise error(f"token endpoint failed the safety check: {exc}") from exc
        try:
            resp = await self._http.post(token_endpoint, data=data)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise error(f"token request failed: {type(exc).__name__}") from exc
        parsed = _parse_model(resp, _TokenResponse, error)
        return parsed.to_token(fallback_refresh=fallback_refresh, fallback_scopes=fallback_scopes)

    async def _get_model(self, url: str, model: type[_M], error: type[Exception]) -> _M:
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise error(f"discovery request failed for {url!r}: {type(exc).__name__}") from exc
        return _parse_model(resp, model, error)


def _parse_model(resp: httpx.Response, model: type[_M], error: type[Exception]) -> _M:
    """Parse a response body through ``model`` — crafted metadata is never trusted raw."""
    try:
        return model.model_validate_json(resp.content)
    except ValidationError as exc:
        raise error(f"malformed response could not be parsed as {model.__name__}") from exc


class _TokenError(BaseModel):
    """The OAuth error body (RFC 6749 §5.2) — parsed, never trusted raw."""

    error: str | None = None


def _token_error_code(resp: httpx.Response) -> str:
    """The OAuth ``error`` code from a token error body, or the status as a string."""
    try:
        parsed = _TokenError.model_validate_json(resp.content)
    except ValidationError:
        return f"http_{resp.status_code}"
    return parsed.error or f"http_{resp.status_code}"


def _well_known_metadata_url(issuer: str) -> str:
    """Derive the RFC 8414 metadata URL from an issuer (well-known path insert)."""
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    well_known = f"/.well-known/oauth-authorization-server{path}"
    return urlunsplit((parts.scheme, parts.netloc, well_known, "", ""))


def _build_authorize_url(
    endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    challenge: str,
) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if scopes:
        query["scope"] = " ".join(scopes)
    parts = urlsplit(endpoint)
    existing = parts.query
    encoded = urlencode(query)
    merged = f"{existing}&{encoded}" if existing else encoded
    return urlunsplit((parts.scheme, parts.netloc, parts.path, merged, ""))
