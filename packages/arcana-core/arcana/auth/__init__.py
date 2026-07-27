"""Unified credentials — OAuth 2.1 flows and the shared ``CredentialProvider``.

The credential foundation both the model layer and the MCP layer consume. The
non-secret half of a connection's auth config lives in ``models.json`` /
``mcps.json`` (see :mod:`arcana.types.auth`); the token bundle lives in the OS
keyring, resolved and refreshed through a :class:`CredentialProvider`.
"""

from arcana.auth.errors import (
    AuthError,
    AuthorizationError,
    DiscoveryError,
    TokenRefreshError,
)
from arcana.auth.oauth import (
    AuthServerMetadata,
    BrowserOpener,
    DeviceAuthResponse,
    DeviceNotifier,
    OAuthClient,
    ProtectedResourceMetadata,
    assert_safe_issuer,
)
from arcana.auth.provider import (
    KEYRING_SERVICE,
    ApiKeyCredentialProvider,
    CredentialProvider,
    OAuthCredentialProvider,
    delete_token,
    load_token,
    save_token,
)

__all__ = [
    # Errors
    "AuthError",
    "AuthorizationError",
    "DiscoveryError",
    "TokenRefreshError",
    # OAuth flows
    "OAuthClient",
    "AuthServerMetadata",
    "ProtectedResourceMetadata",
    "DeviceAuthResponse",
    "BrowserOpener",
    "DeviceNotifier",
    "assert_safe_issuer",
    # Provider seam
    "CredentialProvider",
    "ApiKeyCredentialProvider",
    "OAuthCredentialProvider",
    "KEYRING_SERVICE",
    "load_token",
    "save_token",
    "delete_token",
]
