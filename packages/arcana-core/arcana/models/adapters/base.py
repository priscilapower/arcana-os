"""ModelAdapter ABC and shared wire types."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NotRequired, Required, TypedDict

from pydantic import TypeAdapter, ValidationError

from arcana.models.errors import ModelBadRequestError
from arcana.types._utils import JsonObject

_ARGS_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Best-effort decode of a model-produced tool-call ``arguments`` string.

    Arguments are model-controlled and may be malformed or a non-object; this
    is used when re-serializing tool-call turns into an adapter's wire format,
    where a raw ``json.loads`` would let a bad payload crash the whole request.
    Falls back to an empty object so the turn always round-trips.
    """
    try:
        return _ARGS_ADAPTER.validate_json(raw or "{}")
    except ValidationError:
        return {}


class FunctionCall(TypedDict):
    """The function portion of a tool call returned by a model."""

    name: str
    arguments: str  # JSON-encoded string


class ToolCallResult(TypedDict):
    """A tool call as returned in a CompletionResponse."""

    id: str
    type: str
    function: FunctionCall


class MessageParam(TypedDict):
    """A single chat message in the canonical adapter wire format.

    ``role``/``content`` cover the single-turn path. The optional fields carry
    multi-turn tool state so a tool loop round-trips through history:

    - ``tool_calls`` — set on an ``assistant`` turn that requested tools.
    - ``tool_call_id`` / ``name`` — set on a ``tool`` turn carrying one tool's
      result back to the model (``content`` holds the serialized output).

    Each adapter's translation layer maps these to its SDK's shape (Anthropic
    tool_use / tool_result blocks; OpenAI-style ``tool`` role). Messages without
    the optional fields are ordinary text turns and behave exactly as before.
    """

    role: str
    content: str
    tool_calls: NotRequired[list[ToolCallResult]]
    tool_call_id: NotRequired[str]
    name: NotRequired[str]


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


class OpenAILikeMessage(TypedDict, total=False):
    """An OpenAI-style chat message as sent on the wire by raw-REST adapters.

    ``role`` is always present; the other fields vary by turn:
    - ``content`` — the text, or ``None`` on an assistant turn that only calls tools.
    - ``tool_calls`` — on an assistant turn, reusing ``ToolCallResult``'s
      ``{id, type, function: {name, arguments}}`` shape.
    - ``tool_call_id`` — on a ``tool`` result turn, linking it to its call.
    """

    role: Required[str]
    content: str | None
    tool_calls: list[ToolCallResult]
    tool_call_id: str


@dataclass
class CompletionRequest:
    system: str
    messages: Sequence[MessageParam]
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: Sequence[ToolParam] | None = None
    stream: bool = False
    model_id: str = ""
    metadata: Mapping[str, str] | None = None


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

        Must be overridden in every concrete adapter. The gateway's retry logic
        only catches ``ModelError`` subclasses — a missing override leaks raw
        provider exceptions past the retry net.
        Return the original ``exc`` unchanged for errors that need no translation.
        """
        raise NotImplementedError(f"{type(self).__name__} must override _translate")
