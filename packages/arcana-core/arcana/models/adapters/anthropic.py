"""AnthropicAdapter — Claude models via Anthropic SDK."""

import os
from collections.abc import AsyncGenerator

try:
    import anthropic as _anthropic_mod
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
    ModelChunk,
    ModelHealth,
)
from arcana.models.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelNotFoundError,
    ModelTransientError,
    ModelUnavailableError,
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

    def _translate(self, exc: Exception, model_id: str) -> Exception:
        if isinstance(exc, _anthropic_mod.APIConnectionError):
            return ModelUnavailableError(f"Cannot connect to Anthropic API: {exc}")
        if isinstance(exc, _anthropic_mod.APITimeoutError):
            return ModelTransientError(f"Anthropic request timed out: {exc}")
        if isinstance(exc, _anthropic_mod.AuthenticationError):
            return ModelAuthError(f"Anthropic authentication failed: {exc}")
        if isinstance(exc, _anthropic_mod.PermissionDeniedError):
            return ModelAuthError(f"Anthropic permission denied: {exc}")
        if isinstance(exc, _anthropic_mod.NotFoundError):
            return ModelNotFoundError(f"Anthropic model not found: {model_id!r}")
        if isinstance(exc, _anthropic_mod.BadRequestError):
            return ModelBadRequestError(f"Anthropic rejected the request: {exc}")
        if isinstance(exc, _anthropic_mod.RateLimitError):
            retry_after: float | None = None
            if hasattr(exc, "response"):
                raw = exc.response.headers.get("retry-after")
                if raw:
                    try:
                        retry_after = float(raw)
                    except ValueError:
                        pass
            return ModelTransientError("Anthropic rate limited (HTTP 429)", retry_after=retry_after)
        if isinstance(exc, _anthropic_mod.InternalServerError):
            return ModelTransientError(f"Anthropic server error: {exc}")
        return exc

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
        model = request.model_id or self.model
        client = self._get_client()
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=self._build_messages(request),
                temperature=request.temperature,
            )
        except Exception as exc:
            raise self._translate(exc, model) from exc
        text = next((block.text for block in response.content if isinstance(block, TextBlock)), "")
        return CompletionResponse(
            content=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason or "end_turn",
        )

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]:
        model = request.model_id or self.model
        client = self._get_client()
        try:
            async with client.messages.stream(
                model=model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=self._build_messages(request),
                temperature=request.temperature,
            ) as stream:
                async for text in stream.text_stream:
                    yield ModelChunk(text=text)
                final = await stream.get_final_message()
                yield ModelChunk(
                    text="",
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                )
        except Exception as exc:
            raise self._translate(exc, model) from exc

    async def health_check(self) -> ModelHealth:
        model = self.model
        try:
            self._resolve_key()
            return ModelHealth(healthy=True, model_id=model)
        except Exception as exc:
            return ModelHealth(healthy=False, model_id=model, message=str(exc))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
