"""Credential types shared by the model and MCP layers.

Every outbound connection Arcana makes carries a credential. Historically that
was always a static, user-pasted secret (an API key); OAuth 2.1 adds a second
kind — a short-lived token that is discovered, granted, and refreshed. Which
kind a connection uses is recorded on an :class:`AuthType` discriminator so the
two flow through one shared seam.

Only the non-secret half lives in ``models.json`` / ``mcps.json``: the
:class:`OAuthConfig` (issuer, scopes, grant). The :class:`OAuthToken` bundle —
the actual access/refresh material — is serialized into the OS keyring and never
written to disk in the clear.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from arcana.types._utils import now_utc


class AuthType(StrEnum):
    """How a connection authenticates.

    Records written before this field existed load as :attr:`API_KEY` (the
    Pydantic default), preserving every pre-OAuth connection unchanged; new
    connections created through the CLI default to :attr:`OAUTH` when the target
    advertises it. A ``StrEnum`` so the member *is* its wire value — it
    serializes to the bare string in ``models.json`` / ``mcps.json`` and
    compares equal to it on load.
    """

    API_KEY = "api_key"
    OAUTH = "oauth"


#: The grant flows this implements. ``authorization_code`` is the interactive
#: loopback default; ``device_code`` is the headless fallback (RFC 8628).
OAuthGrant = Literal["authorization_code", "device_code"]


class OAuthConfig(BaseModel):
    """The non-secret half of an OAuth connection — safe to persist to JSON.

    Everything needed to *discover* and *start* a flow, but nothing sensitive:
    the issuer or explicit metadata URL, an optional pre-provisioned
    ``client_id`` (``None`` triggers dynamic client registration), the requested
    scopes, and which grant to run. The resulting token never lives here — it is
    stored in the keyring under the connection's credential reference.
    """

    issuer: str | None = None  # metadata base (RFC 8414 / 9728 discovery)
    metadata_url: str | None = None  # explicit metadata override, skips issuer discovery
    client_id: str | None = None  # None → dynamic client registration (RFC 7591)
    scopes: list[str] = []
    grant: OAuthGrant = "authorization_code"


class OAuthToken(BaseModel):
    """An access/refresh token bundle — keyring-only, never written to ``*.json``.

    ``expires_at`` is absolute (computed from the ``expires_in`` an authorization
    server returns at exchange time), so expiry is checked against the wall clock
    without re-deriving it. A ``None`` ``expires_at`` means the server did not
    declare an expiry — treated as non-expiring, refreshed only reactively on a
    downstream ``401``.
    """

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    scopes: list[str] = []

    def is_expired(self, skew: timedelta) -> bool:
        """True when the token is within ``skew`` of expiry (or already past it).

        A token with no declared ``expires_at`` never reports expired here — it
        is only ever refreshed reactively, when a downstream call returns
        ``401``. ``skew`` is the proactive-refresh margin (see
        ``ARCANA_OAUTH_REFRESH_SKEW_S``): refresh a little early so a token does
        not expire mid-request.
        """
        if self.expires_at is None:
            return False
        return now_utc() >= self.expires_at - skew
