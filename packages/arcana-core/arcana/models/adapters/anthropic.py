"""AnthropicAdapter — Claude models via Anthropic SDK."""

import json
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID

try:
    import anthropic as _anthropic_mod
    from anthropic import AsyncAnthropic
    from anthropic.types import (
        ContentBlockParam,
        TextBlock,
        TextBlockParam,
        ToolResultBlockParam,
        ToolUseBlock,
        ToolUseBlockParam,
    )
    from anthropic.types import (
        Message as AnthropicMessage,
    )
    from anthropic.types import (
        MessageParam as AnthropicMessageParam,
    )
    from anthropic.types import ToolParam as AnthropicToolParam
except ImportError as e:
    raise ImportError("Install arcana-core[anthropic] to use AnthropicAdapter") from e

from arcana.auth import CredentialProvider
from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    FunctionCall,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
    ToolCallResult,
    ToolParam,
    parse_tool_arguments,
)
from arcana.models.connection_store import resolve_api_key
from arcana.models.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelNotFoundError,
    ModelTransientError,
    ModelUnavailableError,
)
from arcana.types.auth import AuthType

_ENV_VAR = "ANTHROPIC_API_KEY"
_PROVIDER_KEY = "anthropic_api_key"

# Placeholder auth values the SDK client is built with when a CredentialProvider
# drives per-request auth: the real credential is injected via ``extra_headers``
# on each call, so the frozen client value is never sent. Building with
# ``auth_token`` (OAuth) vs ``api_key`` selects which header the SDK omits.
_PLACEHOLDER = "arcana-per-request-auth"


def _to_anthropic_tools(tools: list[ToolParam]) -> list[AnthropicToolParam]:
    """Translate canonical ToolParam list to the Anthropic SDK's ToolParam list.

    The shapes are identical (name / description / input_schema); cast bridges
    the JsonObject → InputSchemaTyped gap that only exists at the type-checker level.
    """
    return [
        cast(
            AnthropicToolParam, {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        )
        for t in tools
    ]


class AnthropicAdapter(ModelAdapter):
    """
    Connects to Anthropic's API.

    API key precedence (see ``resolve_api_key`` in ``connection_store``):
      1. ``api_key`` argument
      2. Connection-id keyring entry (``{connection_id}_api_key``)
      3. ``ANTHROPIC_API_KEY`` environment variable
      4. Provider-named keyring entry (``anthropic_api_key``)

    Usage:
        adapter = AnthropicAdapter(model="claude-sonnet-4-6")
        response = await adapter.complete(request)
    """

    supports_tools = True

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        connection_id: UUID | None = None,
        *,
        credentials: CredentialProvider | None = None,
        auth_type: AuthType = AuthType.API_KEY,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._connection_id = connection_id
        self._credentials = credentials
        self._auth_type = auth_type
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
            if self._credentials is not None:
                # Per-request auth: build with a placeholder for the right scheme
                # so the SDK omits the *other* auth header, then override it on
                # each call. OAuth → auth_token (Authorization: Bearer); api_key →
                # api_key (x-api-key).
                if self._auth_type is AuthType.OAUTH:
                    self._client = AsyncAnthropic(auth_token=_PLACEHOLDER)
                else:
                    self._client = AsyncAnthropic(api_key=_PLACEHOLDER)
            else:
                key = self._api_key or self._resolve_key()
                self._client = AsyncAnthropic(api_key=key)
        return self._client

    def _resolve_key(self) -> str:
        key = resolve_api_key(self._connection_id, _ENV_VAR, _PROVIDER_KEY)
        if key:
            return key
        raise ValueError(
            "Anthropic API key not found. Set ANTHROPIC_API_KEY or run: arcana connect model anthropic --api-key <key>"
        )

    async def _auth_headers(self) -> dict[str, str] | None:
        """The per-request auth header, or ``None`` when the client carries the key.

        Legacy (no provider): ``None`` — the frozen client already authenticates.
        With a provider: the freshly-resolved credential, sent as ``Authorization:
        Bearer`` for OAuth or ``x-api-key`` for an API key.
        """
        if self._credentials is None:
            return None
        token = await self._credentials.get_token()
        if self._auth_type is AuthType.OAUTH:
            return {"Authorization": f"Bearer {token}"}
        return {"x-api-key": token}

    def _build_messages(self, request: CompletionRequest) -> list[AnthropicMessageParam]:
        result: list[AnthropicMessageParam] = []
        pending_results: list[ContentBlockParam] = []

        def _flush() -> None:
            if pending_results:
                result.append(AnthropicMessageParam(role="user", content=list(pending_results)))
                pending_results.clear()

        for msg in request.messages:
            role = msg["role"]
            content = msg["content"]
            if role == "tool":
                pending_results.append(
                    ToolResultBlockParam(
                        type="tool_result",
                        tool_use_id=msg.get("tool_call_id", ""),
                        content=content,
                    )
                )
                continue
            _flush()
            tool_calls = msg.get("tool_calls")
            if role == "assistant" and tool_calls:
                blocks: list[ContentBlockParam] = []
                if content:
                    blocks.append(TextBlockParam(type="text", text=content))
                for tc in tool_calls:
                    blocks.append(
                        ToolUseBlockParam(
                            type="tool_use",
                            id=tc["id"],
                            name=tc["function"]["name"],
                            input=parse_tool_arguments(tc["function"]["arguments"]),
                        )
                    )
                result.append(AnthropicMessageParam(role="assistant", content=blocks))
            elif role == "user":
                result.append(AnthropicMessageParam(role="user", content=content))
            elif role == "assistant":
                result.append(AnthropicMessageParam(role="assistant", content=content))
            elif role == "system":
                result.append(AnthropicMessageParam(role="system", content=content))
        _flush()
        return result

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = request.model_id or self.model
        client = self._get_client()
        messages = self._build_messages(request)
        tools = _to_anthropic_tools(list(request.tools)) if request.tools else None

        async def _create(headers: dict[str, str] | None) -> AnthropicMessage:
            if tools is not None:
                return await client.messages.create(
                    model=model,
                    max_tokens=request.max_tokens,
                    system=request.system,
                    messages=messages,
                    temperature=request.temperature,
                    tools=tools,
                    extra_headers=headers,
                )
            return await client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
                temperature=request.temperature,
                extra_headers=headers,
            )

        response: AnthropicMessage | None = None
        for attempt in (0, 1):
            headers = await self._auth_headers()
            try:
                response = await _create(headers)
                break
            except Exception as exc:
                if await self._reauth(exc, model, attempt):
                    continue
                raise self._translate(exc, model) from exc
        assert response is not None  # the loop either assigns or raises
        text = next((block.text for block in response.content if isinstance(block, TextBlock)), "")
        tool_calls: list[ToolCallResult] | None = None
        tool_use_blocks = [b for b in response.content if isinstance(b, ToolUseBlock)]
        if tool_use_blocks:
            tool_calls = [
                ToolCallResult(
                    id=b.id,
                    type="function",
                    function=FunctionCall(name=b.name, arguments=json.dumps(b.input)),
                )
                for b in tool_use_blocks
            ]
        return CompletionResponse(
            content=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
        )

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]:
        model = request.model_id or self.model
        client = self._get_client()
        messages = self._build_messages(request)
        tools = _to_anthropic_tools(list(request.tools)) if request.tools else None

        def _open(headers: dict[str, str] | None):
            if tools is not None:
                return client.messages.stream(
                    model=model,
                    max_tokens=request.max_tokens,
                    system=request.system,
                    messages=messages,
                    temperature=request.temperature,
                    tools=tools,
                    extra_headers=headers,
                )
            return client.messages.stream(
                model=model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
                temperature=request.temperature,
                extra_headers=headers,
            )

        # A reactive refresh-retry only makes sense before any token is emitted;
        # an auth failure occurs at stream open, so once text has flowed the
        # error is surfaced as-is (output cannot be cleanly replayed).
        for attempt in (0, 1):
            headers = await self._auth_headers()
            started = False
            try:
                async with _open(headers) as stream:
                    async for text in stream.text_stream:
                        started = True
                        yield ModelChunk(text=text)
                    final = await stream.get_final_message()
                    yield ModelChunk(
                        text="",
                        input_tokens=final.usage.input_tokens,
                        output_tokens=final.usage.output_tokens,
                    )
                return
            except Exception as exc:
                if not started and await self._reauth(exc, model, attempt):
                    continue
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
