"""The shared credential seam both adapter families depend on.

A :class:`CredentialProvider` hands an adapter a *valid* bearer/API key on
demand and knows how to react to a downstream ``401`` — nothing more. Adapters
depend only on this Protocol; they never touch the keyring or refresh logic, so
the model layer and the MCP layer share one token-lifecycle implementation
instead of forking it.

Two implementations back it:

* :class:`ApiKeyCredentialProvider` — wraps a static-key resolver and never
  refreshes (the preserved API-key path).
* :class:`OAuthCredentialProvider` — resolves an :class:`OAuthToken` from the
  keyring, refreshes it **proactively** (before expiry − skew) and **reactively**
  (on a ``401``), persists rotated refresh tokens, and serialises concurrent
  refreshes behind a per-connection lock.

Both fail **closed**: a missing or unrefreshable credential raises
:class:`AuthError`, never returns an empty string that would fall through to an
unauthenticated request.
"""

import asyncio
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import keyring

from arcana.auth.config import REFRESH_SKEW
from arcana.auth.errors import AuthError, TokenRefreshError
from arcana.auth.oauth import OAuthClient
from arcana.observability import emit_auth_event
from arcana.types.auth import OAuthConfig, OAuthToken

#: The OS keyring service every Arcana credential lives under — shared by the
#: model connection store and the MCP registry so tokens sit beside API keys.
KEYRING_SERVICE = "arcana"


# ---------------------------------------------------------------------------
# Keyring token helpers — the one place OAuthToken is (de)serialised to keyring.
# ---------------------------------------------------------------------------


def load_token(ref: str, *, service: str = KEYRING_SERVICE) -> OAuthToken | None:
    """Read and parse the ``OAuthToken`` stored under ``ref``, or ``None``.

    A malformed or absent entry returns ``None`` rather than raising, so a
    corrupted keyring entry surfaces as "not authenticated" (fail-closed) rather
    than a crash.
    """
    try:
        raw = keyring.get_password(service, ref)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return OAuthToken.model_validate_json(raw)
    except ValueError:
        return None


def save_token(ref: str, token: OAuthToken, *, service: str = KEYRING_SERVICE) -> None:
    """Serialise ``token`` to the keyring under ``ref`` (never to ``*.json``)."""
    keyring.set_password(service, ref, token.model_dump_json())


def delete_token(ref: str, *, service: str = KEYRING_SERVICE) -> None:
    """Remove the token under ``ref``. No-op if the entry does not exist."""
    try:
        keyring.delete_password(service, ref)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CredentialProvider(Protocol):
    """A source of a valid credential for one connection.

    ``get_token`` returns a bearer/API key ready to send; ``on_unauthorized`` is
    called after a downstream ``401`` and returns ``True`` when it refreshed the
    credential and a single retry is worth attempting.
    """

    async def get_token(self) -> str: ...

    async def on_unauthorized(self) -> bool: ...


# ---------------------------------------------------------------------------
# API-key provider (the preserved static-secret path)
# ---------------------------------------------------------------------------


class ApiKeyCredentialProvider:
    """Wraps a static-key resolver; never refreshes.

    ``resolver`` returns the key (typically ``resolve_api_key`` bound to a
    connection) or ``None``. A ``None`` result fails closed with
    :class:`AuthError`. ``on_unauthorized`` always returns ``False`` — a static
    key cannot be refreshed, so retrying is pointless.
    """

    def __init__(self, resolver: Callable[[], str | None]) -> None:
        self._resolver = resolver

    async def get_token(self) -> str:
        key = self._resolver()
        if not key:
            raise AuthError("no API key configured for this connection")
        return key

    async def on_unauthorized(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# OAuth provider
# ---------------------------------------------------------------------------

#: Builds an :class:`OAuthClient` for a refresh. Injectable so a test drives a
#: fake authorization server without real network.
OAuthClientFactory = Callable[[], OAuthClient]


class OAuthCredentialProvider:
    """Resolves and refreshes an OAuth access token for one connection.

    Keyed by ``credential_ref`` in the ``"arcana"`` keyring. A per-instance
    :class:`asyncio.Lock` collapses concurrent refreshes into one — a stampede of
    calls that all see an expiring token triggers a single refresh, and the
    rotated refresh token is persisted before the new access token is handed out.
    """

    def __init__(
        self,
        config: OAuthConfig,
        credential_ref: str,
        *,
        service: str = KEYRING_SERVICE,
        client_factory: OAuthClientFactory = OAuthClient,
        reauth_hint: str | None = None,
    ) -> None:
        self._config = config
        self._ref = credential_ref
        self._service = service
        self._client_factory = client_factory
        # A caller-supplied, connection-specific command the user can run to
        # re-authenticate (e.g. "arcana providers login claude"). Surfaced in the
        # not-authenticated / refresh-failed errors so the message is actionable
        # and points at the right connection — never asks the user to re-`add`.
        self._reauth_hint = reauth_hint
        self._lock = asyncio.Lock()

    def _not_authenticated(self) -> str:
        if self._reauth_hint:
            return f"connection is not authenticated — run `{self._reauth_hint}` to sign in"
        return "connection is not authenticated — sign in again"

    def _load(self) -> OAuthToken | None:
        return load_token(self._ref, service=self._service)

    def _save(self, token: OAuthToken) -> None:
        save_token(self._ref, token, service=self._service)

    async def get_token(self) -> str:
        """Return a valid access token, refreshing proactively near expiry."""
        token = self._load()
        if token is None:
            raise AuthError(self._not_authenticated())
        if not token.is_expired(REFRESH_SKEW):
            return token.access_token
        return await self._refresh(token, require_expired=True)

    async def on_unauthorized(self) -> bool:
        """Refresh once after a downstream ``401``; ``True`` if a retry is worth it.

        Unlike the proactive path this refreshes even when the token is not yet
        time-expired — a ``401`` means the server rejected it (revocation, clock
        skew), so expiry is not the signal here.
        """
        before = self._load()
        if before is None or not before.refresh_token:
            return False
        try:
            await self._refresh(before, require_expired=False)
        except AuthError:
            return False
        return True

    async def _refresh(self, seen: OAuthToken, *, require_expired: bool) -> str:
        """Refresh under the lock, double-checking so only one refresh runs.

        ``seen`` is the token the caller observed as expiring/rejected. After
        taking the lock we reload: if another task already rotated it (the stored
        access token changed) we reuse that result. On the *proactive* path
        (``require_expired``) we also skip a token that is no longer expiring;
        the *reactive* path (a ``401``) refreshes regardless of expiry.
        """
        async with self._lock:
            current = self._load()
            if current is None:
                raise TokenRefreshError(self._not_authenticated())
            if current.access_token != seen.access_token:
                return current.access_token  # another task already rotated it
            if require_expired and not current.is_expired(REFRESH_SKEW):
                return current.access_token
            try:
                async with self._client_factory() as client:
                    rotated = await client.refresh(self._config, current)
            except AuthError as exc:
                emit_auth_event(self._ref, phase="refresh_failed", success=False)
                # A failed refresh means the stored credentials can no longer be
                # renewed — re-authentication is the fix, so surface that (with the
                # connection-specific command) rather than the raw refresh error.
                hint = f" — run `{self._reauth_hint}` to sign in again" if self._reauth_hint else ""
                raise TokenRefreshError(f"could not refresh credentials{hint}") from exc
            self._save(rotated)  # persist rotation before the token is used
            emit_auth_event(self._ref, phase="refreshed", success=True)
            return rotated.access_token
