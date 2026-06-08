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
    from openai import AsyncOpenAI
except ImportError as e:
    raise ImportError("Install arcana-core[openai] to use OpenAICompatAdapter") from e

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    FunctionCall,
    MessageParam,
    ModelAdapter,
    ModelHealth,
    ToolCallResult,
    ToolParam,
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
        client = self._get_client()
        kwargs: CompletionCreateParamsNonStreaming = {
            "model": self.model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            kwargs["tools"] = _to_openai_tools(request.tools)
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)
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

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        client = self._get_client()
        kwargs: CompletionCreateParamsStreaming = {
            "model": self.model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
        }

        response = await client.chat.completions.create(**kwargs)
        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def health_check(self) -> ModelHealth:
        try:
            client = self._get_client()
            models_page = await client.models.list()
            model_ids = [m.id for m in models_page.data]
            available = self.model in model_ids or any(mid.startswith(self.model.split(":")[0]) for mid in model_ids)
            return ModelHealth(
                healthy=available,
                model_id=self.model,
                message="" if available else f"Available models: {', '.join(model_ids)}",
            )
        except _openai_mod.APIConnectionError as exc:
            return ModelHealth(healthy=False, model_id=self.model, message=f"Connection error: {exc}")
        except Exception as exc:
            return ModelHealth(healthy=False, model_id=self.model, message=str(exc))
