"""AnthropicAdapter — Claude models via Anthropic SDK."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

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
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            key = self._api_key or self._resolve_key()
            self._client = anthropic.AsyncAnthropic(api_key=key)
        return self._client

    def _resolve_key(self) -> str:
        import os

        key = os.getenv("ANTHROPIC_API_KEY")
        if key:
            return key
        try:
            import keyring

            key = keyring.get_password("arcana", "anthropic_api_key")
            if key:
                return key
        except Exception:
            pass
        raise ValueError(
            "Anthropic API key not found. Set ANTHROPIC_API_KEY or run: arcana connect model anthropic --api-key <key>"
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=request.messages,
            temperature=request.temperature,
        )
        return CompletionResponse(
            content=response.content[0].text,
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
            messages=request.messages,
            temperature=request.temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def health_check(self) -> ModelHealth:
        try:
            self._resolve_key()
            return ModelHealth(healthy=True, model_id=self.model)
        except Exception as e:
            return ModelHealth(healthy=False, model_id=self.model, message=str(e))
