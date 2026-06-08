"""AnthropicAdapter — Claude models via Anthropic SDK."""

import os
from collections.abc import AsyncGenerator

try:
    from anthropic import AsyncAnthropic
    from anthropic.types import MessageParam as AnthropicMessageParam
    from anthropic.types import TextBlock
except ImportError as e:
    raise ImportError("Install arcana-core[anthropic] to use AnthropicAdapter") from e

import keyring

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    ModelAdapter,
    ModelHealth,
)


class AnthropicAdapter(ModelAdapter):
    """
    Connects to Anthropic's API.
    API key resolved from: argument → ANTHROPIC_API_KEY env → keyring.

    Usage:
        adapter = AnthropicAdapter(model="claude-sonnet-4-6")
        response = await adapter.complete(request)
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            key = self._api_key or self._resolve_key()
            self._client = AsyncAnthropic(api_key=key)
        return self._client

    def _resolve_key(self) -> str:
        key = os.getenv("ANTHROPIC_API_KEY")
        if key:
            return key
        try:
            key = keyring.get_password("arcana", "anthropic_api_key")
            if key:
                return key
        except Exception:
            pass
        raise ValueError(
            "Anthropic API key not found. Set ANTHROPIC_API_KEY or run: arcana connect model anthropic --api-key <key>"
        )

    def _build_messages(self, request: CompletionRequest) -> list[AnthropicMessageParam]:
        result: list[AnthropicMessageParam] = []
        for msg in request.messages:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                result.append(AnthropicMessageParam(role="user", content=content))
            elif role == "assistant":
                result.append(AnthropicMessageParam(role="assistant", content=content))
            elif role == "system":
                result.append(AnthropicMessageParam(role="system", content=content))
        return result

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=self._build_messages(request),
            temperature=request.temperature,
        )
        text = next((block.text for block in response.content if isinstance(block, TextBlock)), "")
        return CompletionResponse(
            content=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason or "end_turn",
        )

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        client = self._get_client()
        async with client.messages.stream(
            model=self.model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=self._build_messages(request),
            temperature=request.temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def health_check(self) -> ModelHealth:
        try:
            self._resolve_key()
            return ModelHealth(healthy=True, model_id=self.model)
        except Exception as exc:
            return ModelHealth(healthy=False, model_id=self.model, message=str(exc))
