"""Model error taxonomy — normalized exceptions raised by adapters.

Adapters translate provider-specific failures into these classes so
the gateway's retry logic is provider-agnostic.
"""


class ModelError(Exception):
    """Base class for all model errors."""


class ModelTransientError(ModelError):
    """Retryable error: timeout, connection reset, 429, 500/502/503."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelUnavailableError(ModelTransientError):
    """Connection refused — server not running or still cold (Ollama, local endpoints)."""


class ModelAuthError(ModelError):
    """Fatal: 401 / 403. Retrying is pointless until credentials change."""


class ModelBadRequestError(ModelError):
    """Fatal: 400, malformed request, context-length exceeded."""


class ModelNotFoundError(ModelError):
    """Fatal: model not pulled or unknown model ID.

    The message should tell the user how to fix it (e.g. `ollama pull <model>`).
    """
