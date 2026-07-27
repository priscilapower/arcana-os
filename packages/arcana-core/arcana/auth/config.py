"""Tunables for the OAuth credential machinery.

The refresh margin, the loopback listener timeout, and the device-grant poll
interval ship with sensible defaults but are **overridable via environment
variables**, so an operator can tune the flows without a code change.
:class:`OAuthSettings` is a ``pydantic-settings`` model: it reads
``ARCANA_OAUTH_*`` env vars once at import (typed, coerced, bounds-validated so a
bad value fails fast) into the module constants the package exposes.
"""

from datetime import timedelta

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OAuthSettings(BaseSettings):
    """OAuth flow knobs, overridable via ``ARCANA_OAUTH_*`` env vars.

    Read once into the module constants below. Unrelated variables that share
    the prefix are ignored rather than rejected.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_OAUTH_", extra="ignore")

    # Proactive-refresh margin (seconds): refresh a token once it is within this
    # window of expiry so it never expires mid-request.
    refresh_skew_s: float = Field(default=60.0, gt=0.0)

    # How long the loopback listener waits for the browser callback before it
    # gives up and fails closed. A stalled login must not hang the CLI forever.
    listener_timeout_s: float = Field(default=300.0, gt=0.0)

    # Overall ceiling on the device-authorization poll loop (seconds). Polling
    # stops at this bound even if the server's declared expiry is longer.
    device_timeout_s: float = Field(default=300.0, gt=0.0)

    # Fallback poll interval (seconds) for the device grant when the server does
    # not return one. A floor is also enforced against a hostile tiny interval.
    device_poll_interval_s: float = Field(default=5.0, gt=0.0)

    # Per-request timeout (seconds) for every call to a discovery, registration,
    # or token endpoint.
    http_timeout_s: float = Field(default=30.0, gt=0.0)


_SETTINGS = OAuthSettings()

REFRESH_SKEW = timedelta(seconds=_SETTINGS.refresh_skew_s)
LISTENER_TIMEOUT_S = _SETTINGS.listener_timeout_s
DEVICE_TIMEOUT_S = _SETTINGS.device_timeout_s
DEVICE_POLL_INTERVAL_S = _SETTINGS.device_poll_interval_s
HTTP_TIMEOUT_S = _SETTINGS.http_timeout_s
