"""OllamaAdapter — local models via Ollama."""

import json
from collections.abc import AsyncGenerator

import httpx

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    ModelAdapter,
    ModelHealth,
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

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        messages = self._build_messages(request)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        response = await self._client.post(f"{self.endpoint}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        return CompletionResponse(
            content=data["message"]["content"],
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
        )

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        messages = self._build_messages(request)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": request.temperature},
        }
        async with self._client.stream("POST", f"{self.endpoint}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    chunk = json.loads(line)
                    if content := chunk.get("message", {}).get("content"):
                        yield content
                    if chunk.get("done"):
                        break

    async def health_check(self) -> ModelHealth:
        try:
            response = await self._client.get(f"{self.endpoint}/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            available = self.model in models or any(m.startswith(self.model.split(":")[0]) for m in models)
            return ModelHealth(
                healthy=available,
                model_id=self.model,
                message=f"Available models: {', '.join(models)}" if not available else "",
            )
        except Exception as e:
            return ModelHealth(healthy=False, model_id=self.model, message=str(e))

    def _build_messages(self, request: CompletionRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(request.messages)
        return messages
