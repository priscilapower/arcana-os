"""ModelAdapter ABC."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict


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
    input_schema: dict[str, Any]


@dataclass
class CompletionRequest:
    system: str
    messages: Sequence[MessageParam]
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: Sequence[ToolParam] | None = None
    stream: bool = False


@dataclass
class CompletionResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: Sequence[ToolCallResult] | None = None
    stop_reason: str = "end_turn"


@dataclass
class ModelHealth:
    healthy: bool
    model_id: str
    message: str = ""


class ModelAdapter(ABC):
    """Every LLM backend implements this interface."""

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    @abstractmethod
    def stream(self, request: CompletionRequest) -> AsyncGenerator[str, None]: ...

    @abstractmethod
    async def health_check(self) -> ModelHealth: ...
