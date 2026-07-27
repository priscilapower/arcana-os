"""Auth error taxonomy.

The credential layer fails **closed**: every auth problem raises one of these
typed errors, which the two adapter seams then map to a fail-closed result — a
typed model error for providers, a ``ToolResult(success=False)`` for MCP. They
never fall through to an unauthenticated request. Messages here are safe to
surface — they never embed a token.
"""


class AuthError(Exception):
    """Base class for every credential/OAuth failure."""


class TokenRefreshError(AuthError):
    """A refresh attempt failed — the stored refresh token is missing, rejected,
    or the token endpoint erred. The signal that the user must re-authenticate.
    """


class DiscoveryError(AuthError):
    """Metadata discovery or dynamic client registration failed, or returned a
    document that failed validation (SSRF guard, non-https issuer, malformed).
    """


class AuthorizationError(AuthError):
    """The interactive authorization step failed — the user denied consent, the
    callback carried a mismatched ``state`` or an error, or the flow timed out.
    """
