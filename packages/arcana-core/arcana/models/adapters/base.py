"""ModelAdapter ABC and shared wire types."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import TypedDict

from arcana.models.errors import ModelBadRequestError
from arcana.types._utils import JsonObject


class MessageParam(TypedDict):
    """A single chat message in the canonical adapter wire format."""

    role: str
    content: str


class FunctionCall(TypedDict):
    """The function portion of a tool call returned by a model."""

    name: str
    arguments: str  # JSON-encoded string


class ToolCallResult(TypedDict):
    """A tool call as returned in a CompletionResponse."""

    id: str
    type: str
    function: FunctionCall


class ToolParam(TypedDict):
    """Canonical tool definition passed to any adapter.

    Each adapter translates this to its SDK's expected format:
    - Anthropic: passed through directly (same shape)
    - OpenAI / OpenAI-compat: wrapped in {"type": "function", "function": {...}, "parameters": input_schema}
    - Ollama: same as OpenAI-compat
    """

    name: str
    description: str
    input_schema: JsonObject


class OpenAIFunctionDef(TypedDict):
    """The ``function`` sub-object in an OpenAI-style tool definition."""

    name: str
    description: str
    parameters: JsonObject


class OpenAIToolParam(TypedDict):
    """OpenAI-style tool entry as sent on the wire (Ollama, Custom, OpenAI-compat)."""

    type: str  # always "function"
    function: OpenAIFunctionDef


@dataclass
class CompletionRequest:
    system: str
    messages: Sequence[MessageParam]
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: Sequence[ToolParam] | None = None
    stream: bool = False
    model_id: str = ""


@dataclass
class CompletionResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: Sequence[ToolCallResult] | None = None
    stop_reason: str = "end_turn"


@dataclass
class ModelChunk:
    """A single streaming text delta from the gateway.

    ``input_tokens`` and ``output_tokens`` are non-zero only on the final
    chunk (providers differ on when they send usage information).
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelHealth:
    healthy: bool
    model_id: str
    message: str = ""


class ModelAdapter(ABC):
    """Every LLM backend implements this interface."""

    supports_tools: bool = False

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    @abstractmethod
    def stream(self, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]: ...

    @abstractmethod
    async def health_check(self) -> ModelHealth: ...

    async def connect(self) -> None:  # noqa: B027
        """Called once by the gateway after adapter construction. Default: no-op."""

    async def aclose(self) -> None:  # noqa: B027
        """Close underlying connections. Called by the gateway on shutdown. Default: no-op."""

    def _guard_tools(self, request: CompletionRequest) -> None:
        """Raise ModelBadRequestError if the caller passes tools and this adapter can't handle them."""
        if request.tools and not self.supports_tools:
            raise ModelBadRequestError(
                f"{type(self).__name__} does not support tool calls. "
                "Check ModelCapabilities.supports_tools before passing tools."
            )

    def _translate(self, exc: Exception, model_id: str) -> Exception:
        """Translate a provider-specific exception into the shared error taxonomy.

        Override in every concrete adapter. The gateway's retry logic is
        provider-agnostic — it only ever sees ``ModelError`` subclasses.
        Return the original ``exc`` unchanged for errors that need no translation.
        """
        return exc
