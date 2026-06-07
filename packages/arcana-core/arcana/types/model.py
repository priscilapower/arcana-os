"""Model connection types."""

from __future__ import annotations

from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ModelProvider(str, Enum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_COMPAT = "openai_compat"  # LM Studio, any compat endpoint
    CUSTOM = "custom"


class ModelTransport(str, Enum):
    SDK = "sdk"
    API = "api"
    LOCAL_SOCKET = "local_socket"


class ModelCapabilities(BaseModel):
    context_window: int = 4096
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True


class ModelConnection(BaseModel):
    """A connection to an LLM. Persisted to ~/.arcana/connections/models.json."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    provider: ModelProvider
    transport: ModelTransport = ModelTransport.API
    model_id: str  # "hermes-3", "claude-sonnet-4-6"
    endpoint: str = ""  # base URL; empty = use provider default
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)

    # Cost (None for local models)
    cost_per_1k_input_tokens: float | None = None
    cost_per_1k_output_tokens: float | None = None

    # Runtime state (not persisted)
    status: str = "unknown"  # connected | unreachable | authenticating

    @property
    def is_local(self) -> bool:
        return self.provider == ModelProvider.OLLAMA
