"""OllamaAdapter — local models via Ollama."""

import json
from collections.abc import AsyncGenerator
from typing import Any, Required, TypedDict

import httpx

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    FunctionCall,
    MessageParam,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
    OpenAIFunctionDef,
    OpenAIToolParam,
    ToolCallResult,
)
from arcana.models.errors import (
    ModelBadRequestError,
    ModelNotFoundError,
    ModelTransientError,
    ModelUnavailableError,
)

# ---------------------------------------------------------------------------
# Ollama-specific wire-format TypedDicts
# ---------------------------------------------------------------------------


class _OllamaOptions(TypedDict):
    temperature: float


class _OllamaChatPayload(TypedDict, total=False):
    model: Required[str]
    messages: Required[list[MessageParam]]
    stream: Required[bool]
    options: Required[_OllamaOptions]
    tools: list[OpenAIToolParam]


class _OllamaToolCallFn(TypedDict):
    """Function sub-object inside an Ollama tool_call entry (always present)."""

    name: str
    arguments: Any  # Ollama returns arguments as a dict, not a JSON string


class _OllamaToolCall(TypedDict):
    """A single tool_call entry in an Ollama /api/chat response (always has function)."""

    function: _OllamaToolCallFn


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class OllamaAdapter(ModelAdapter):
    """
    Connects to a local Ollama instance.
    Default endpoint: http://localhost:11434

    Usage:
        adapter = OllamaAdapter(model="hermes-3")
        response = await adapter.complete(request)
    """

    supports_tools = True

    def __init__(
        self,
        model: str,
        endpoint: str = "http://localhost:11434",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _translate(self, exc: Exception, model_id: str) -> Exception:
        if isinstance(exc, httpx.ConnectError):
            return ModelUnavailableError(
                f"Cannot connect to Ollama at the configured endpoint. Is Ollama running? (error: {exc})"
            )
        if isinstance(exc, httpx.TimeoutException):
            return ModelTransientError(f"Ollama request timed out: {exc}")
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 404:
                return ModelNotFoundError(f"Model {model_id!r} not found. Pull it first: ollama pull {model_id}")
            if status == 400:
                return ModelBadRequestError(f"Ollama rejected the request (HTTP 400): {exc}")
            if status == 429:
                retry_after: float | None = None
                raw = exc.response.headers.get("Retry-After")
                if raw:
                    try:
                        retry_after = float(raw)
                    except ValueError:
                        pass
                return ModelTransientError("Ollama rate limited (HTTP 429)", retry_after=retry_after)
            if 500 <= status < 600:
                return ModelTransientError(f"Ollama server error (HTTP {status}): {exc}")
        return exc

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = request.model_id or self.model
        messages = self._build_messages(request)
        payload: _OllamaChatPayload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        if request.tools:
            payload["tools"] = [
                OpenAIToolParam(
                    type="function",
                    function=OpenAIFunctionDef(
                        name=t["name"],
                        description=t["description"],
                        parameters=t["input_schema"],
                    ),
                )
                for t in request.tools
            ]
        try:
            response = await self._client.post(f"{self.endpoint}/api/chat", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise self._translate(exc, model) from exc
        data = response.json()
        msg = data["message"]
        tool_calls: list[ToolCallResult] | None = None
        raw_calls: list[_OllamaToolCall] = msg.get("tool_calls") or []
        if raw_calls:
            tool_calls = [
                ToolCallResult(
                    id=str(i),
                    type="function",
                    function=FunctionCall(
                        name=str(tc["function"]["name"] or ""),
                        arguments=json.dumps(tc["function"]["arguments"])
                        if isinstance(tc["function"]["arguments"], dict)
                        else str(tc["function"]["arguments"] or ""),
                    ),
                )
                for i, tc in enumerate(raw_calls)
            ]
        return CompletionResponse(
            content=msg.get("content") or "",
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            tool_calls=tool_calls,
        )

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]:
        model = request.model_id or self.model
        messages = self._build_messages(request)
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": request.temperature},
        }
        try:
            async with self._client.stream("POST", f"{self.endpoint}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        chunk = json.loads(line)
                        if content := chunk.get("message", {}).get("content"):
                            yield ModelChunk(text=content)
                        if chunk.get("done"):
                            yield ModelChunk(
                                text="",
                                input_tokens=chunk.get("prompt_eval_count", 0),
                                output_tokens=chunk.get("eval_count", 0),
                            )
                            break
        except Exception as exc:
            raise self._translate(exc, model) from exc

    async def health_check(self) -> ModelHealth:
        model = self.model
        try:
            response = await self._client.get(f"{self.endpoint}/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            available = model in models or any(m.startswith(model.split(":")[0]) for m in models)
            return ModelHealth(
                healthy=available,
                model_id=model,
                message=f"Available models: {', '.join(models)}" if not available else "",
            )
        except Exception as e:
            return ModelHealth(healthy=False, model_id=model, message=str(e))

    def _build_messages(self, request: CompletionRequest) -> list[MessageParam]:
        messages: list[MessageParam] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(request.messages)
        return messages
