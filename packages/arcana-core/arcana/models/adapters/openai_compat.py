"""OpenAICompatAdapter — any OpenAI-compatible endpoint (LM Studio, vLLM, LocalAI, etc.)."""

import os
from collections.abc import AsyncGenerator, Sequence

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.completion_create_params import (
    CompletionCreateParamsNonStreaming,
    CompletionCreateParamsStreaming,
)
from openai.types.shared_params import FunctionDefinition

try:
    import openai as _openai_mod
    from openai import AsyncOpenAI, AsyncStream
    from openai.types.chat import ChatCompletionChunk
except ImportError as e:
    raise ImportError("Install arcana-core[openai] to use OpenAICompatAdapter") from e

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    FunctionCall,
    MessageParam,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
    ToolCallResult,
    ToolParam,
)
from arcana.models.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelNotFoundError,
    ModelTransientError,
    ModelUnavailableError,
)

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "content_filter",
    "function_call": "tool_use",
}


def _to_openai_message(msg: MessageParam) -> ChatCompletionMessageParam:
    role = msg["role"]
    content = msg["content"]
    if role == "system":
        return ChatCompletionSystemMessageParam(role="system", content=content)
    if role == "assistant":
        return ChatCompletionAssistantMessageParam(role="assistant", content=content)
    return ChatCompletionUserMessageParam(role="user", content=content)


def _to_openai_tools(tools: Sequence[ToolParam]) -> list[ChatCompletionToolParam]:
    return [
        ChatCompletionToolParam(
            type="function",
            function=FunctionDefinition(
                name=t["name"],
                description=t["description"],
                parameters=t["input_schema"],  # type: ignore[arg-type]  # dict invariance at SDK boundary
            ),
        )
        for t in tools
    ]


class OpenAICompatAdapter(ModelAdapter):
    """
    Connects to any OpenAI-compatible chat completions endpoint.

    Targets the standard /v1/chat/completions spec, so it works with
    LM Studio, vLLM, LocalAI, Together AI, Groq, OpenRouter, and plain OpenAI.

    Usage:
        adapter = OpenAICompatAdapter(
            model="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF",
            base_url="http://localhost:1234/v1",
        )
        response = await adapter.complete(request)
    """

    supports_tools = True

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:1234/v1",
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: AsyncOpenAI | None = None

    def _translate(self, exc: Exception, model_id: str) -> Exception:
        if isinstance(exc, _openai_mod.APIConnectionError):
            return ModelUnavailableError(f"Cannot connect to the endpoint: {exc}")
        if isinstance(exc, _openai_mod.APITimeoutError):
            return ModelTransientError(f"Request timed out: {exc}")
        if isinstance(exc, _openai_mod.AuthenticationError):
            return ModelAuthError(f"Authentication failed: {exc}")
        if isinstance(exc, _openai_mod.PermissionDeniedError):
            return ModelAuthError(f"Permission denied: {exc}")
        if isinstance(exc, _openai_mod.NotFoundError):
            return ModelNotFoundError(f"Model not found: {model_id!r}")
        if isinstance(exc, _openai_mod.BadRequestError):
            return ModelBadRequestError(f"Bad request: {exc}")
        if isinstance(exc, _openai_mod.RateLimitError):
            retry_after: float | None = None
            if hasattr(exc, "response"):
                raw = exc.response.headers.get("retry-after")
                if raw:
                    try:
                        retry_after = float(raw)
                    except ValueError:
                        pass
            return ModelTransientError("Rate limited (HTTP 429)", retry_after=retry_after)
        if isinstance(exc, _openai_mod.InternalServerError):
            return ModelTransientError(f"Server error: {exc}")
        return exc

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            key = self._api_key or os.getenv("OPENAI_API_KEY") or "not-needed"
            self._client = AsyncOpenAI(
                api_key=key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    def _build_messages(self, request: CompletionRequest) -> list[ChatCompletionMessageParam]:
        messages: list[ChatCompletionMessageParam] = []
        if request.system:
            messages.append(ChatCompletionSystemMessageParam(role="system", content=request.system))
        messages.extend(_to_openai_message(m) for m in request.messages)
        return messages

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = request.model_id or self.model
        client = self._get_client()
        kwargs: CompletionCreateParamsNonStreaming = {
            "model": model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            kwargs["tools"] = _to_openai_tools(request.tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._translate(exc, model) from exc

        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCallResult] | None = None
        if message.tool_calls:
            tool_calls = [
                ToolCallResult(
                    id=tc.id,
                    type=tc.type,
                    function=FunctionCall(name=tc.function.name, arguments=tc.function.arguments),
                )
                for tc in message.tool_calls
                if isinstance(tc, ChatCompletionMessageFunctionToolCall)
            ]

        return CompletionResponse(
            content=message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            tool_calls=tool_calls,
            stop_reason=_STOP_REASON_MAP.get(choice.finish_reason or "stop", "end_turn"),
        )

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]:
        model = request.model_id or self.model
        client = self._get_client()
        kwargs: CompletionCreateParamsStreaming = {
            "model": model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        response: AsyncStream[ChatCompletionChunk] | None = None
        try:
            response = await client.chat.completions.create(**kwargs)
            async for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield ModelChunk(text=delta.content)
                if chunk.usage:
                    yield ModelChunk(
                        text="",
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    )
        except Exception as exc:
            raise self._translate(exc, model) from exc
        finally:
            if response is not None:
                await response.close()

    async def health_check(self) -> ModelHealth:
        model = self.model
        try:
            client = self._get_client()
            models_page = await client.models.list()
            model_ids = [m.id for m in models_page.data]
            available = model in model_ids or any(mid.startswith(model.split(":")[0]) for mid in model_ids)
            return ModelHealth(
                healthy=available,
                model_id=model,
                message="" if available else f"Available models: {', '.join(model_ids)}",
            )
        except _openai_mod.APIConnectionError as exc:
            return ModelHealth(healthy=False, model_id=model, message=f"Connection error: {exc}")
        except Exception as exc:
            return ModelHealth(healthy=False, model_id=model, message=str(exc))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
