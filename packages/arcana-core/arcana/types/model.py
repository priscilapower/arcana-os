"""Model connection types."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc


class ModelProvider(StrEnum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_COMPAT = "openai_compat"  # LM Studio, any compat endpoint
    CUSTOM = "custom"


class ModelTransport(StrEnum):
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
    default_model: str | None = None  # per-connection fallback when reference omits model_id
    endpoint: str = ""  # base URL; empty = use provider default
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)

    # Credential reference - keyring key
    credential_ref: str | None = None

    # Custom headers (custom adapter only)
    headers: dict[str, str] = Field(default_factory=dict)

    # Cost (None for local models)
    cost_per_1k_input_tokens: float | None = None
    cost_per_1k_output_tokens: float | None = None

    # Lifecycle timestamps
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    # Runtime state (not persisted)
    status: str = "unknown"  # connected | unreachable | authenticating

    @property
    def is_local(self) -> bool:
        return self.provider == ModelProvider.OLLAMA
