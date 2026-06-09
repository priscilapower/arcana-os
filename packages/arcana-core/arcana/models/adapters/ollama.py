"""OllamaAdapter — local models via Ollama."""

import json
from collections.abc import AsyncGenerator

import httpx

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    MessageParam,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
)
from arcana.models.errors import (
    ModelBadRequestError,
    ModelNotFoundError,
    ModelTransientError,
    ModelUnavailableError,
)


class OllamaAdapter(ModelAdapter):
    """
    Connects to a local Ollama instance.
    Default endpoint: http://localhost:11434

    Usage:
        adapter = OllamaAdapter(model="hermes-3")
        response = await adapter.complete(request)
    """

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
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        try:
            response = await self._client.post(f"{self.endpoint}/api/chat", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise self._translate(exc, model) from exc
        data = response.json()
        return CompletionResponse(
            content=data["message"]["content"],
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
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
