"""Model gateway, adapters, and supporting types."""

from arcana.models.adapters.anthropic import AnthropicAdapter
from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    MessageParam,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
    ToolCallResult,
    ToolParam,
)
from arcana.models.adapters.custom_api import CustomAPIAdapter
from arcana.models.adapters.ollama import OllamaAdapter
from arcana.models.adapters.openai_compat import OpenAICompatAdapter
from arcana.models.connection_store import ConnectionStore
from arcana.models.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelError,
    ModelNotFoundError,
    ModelTransientError,
    ModelUnavailableError,
)
from arcana.models.gateway import (
    DEFAULT_PROVIDERS,
    ModelGateway,
    ProviderEntry,
    ProviderRegistry,
    RetryPolicy,
)
from arcana.models.pricing import (
    DEFAULT_PRICING,
    CostEvent,
    PricingTable,
    Usage,
)

__all__ = [
    # Adapters
    "AnthropicAdapter",
    "CustomAPIAdapter",
    "OllamaAdapter",
    "OpenAICompatAdapter",
    "ModelAdapter",
    # Base types
    "CompletionRequest",
    "CompletionResponse",
    "MessageParam",
    "ModelChunk",
    "ModelHealth",
    "ToolCallResult",
    "ToolParam",
    # Gateway
    "ModelGateway",
    "RetryPolicy",
    "ProviderRegistry",
    "ProviderEntry",
    "DEFAULT_PROVIDERS",
    # Connection store
    "ConnectionStore",
    # Errors
    "ModelError",
    "ModelTransientError",
    "ModelUnavailableError",
    "ModelAuthError",
    "ModelBadRequestError",
    "ModelNotFoundError",
    # Pricing
    "Usage",
    "CostEvent",
    "PricingTable",
    "DEFAULT_PRICING",
]
