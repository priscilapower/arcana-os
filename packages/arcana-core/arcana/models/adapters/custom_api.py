"""CustomAPIAdapter — any REST endpoint via raw httpx, no SDK required."""

import json
import os
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    MessageParam,
    ModelAdapter,
    ModelHealth,
)

_RequestBuilder = Callable[[CompletionRequest], dict[str, Any]]
_ResponseParser = Callable[[dict[str, Any]], CompletionResponse]
_StreamChunkParser = Callable[[str], str | None]

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "content_filter",
}


def _openai_like_request_builder(model: str) -> _RequestBuilder:
    def build(request: CompletionRequest) -> dict[str, Any]:
        messages: list[MessageParam] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(request.messages)
        return {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

    return build


def _openai_like_response_parser(data: dict[str, Any]) -> CompletionResponse:
    """
    Tries common REST response shapes in order:
      1. OpenAI-style: choices[0].message.content
      2. Flat: content / text / message
    """
    choices: list[dict[str, Any]] = data.get("choices") or []
    if choices:
        first: dict[str, Any] = choices[0]
        message: dict[str, Any] = first.get("message") or {}
        content: str = str(message.get("content") or "")
        usage: dict[str, Any] = data.get("usage") or {}
        input_tokens: int = int(usage.get("prompt_tokens") or 0)
        output_tokens: int = int(usage.get("completion_tokens") or 0)
        finish_reason: str = str(first.get("finish_reason") or "stop")
        return CompletionResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=_STOP_REASON_MAP.get(finish_reason, "end_turn"),
        )
    flat: str = str(data.get("content") or data.get("text") or data.get("message") or "")
    return CompletionResponse(content=flat)


def _sse_chunk_parser(line: str) -> str | None:
    """
    Parses SSE lines (``data: {...}``) into content tokens.
    Returns None for comments, keep-alives, and ``[DONE]`` sentinels.
    """
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        chunk: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return None
    choices: list[dict[str, Any]] = chunk.get("choices") or []
    if choices:
        delta: dict[str, Any] = choices[0].get("delta") or {}
        token = delta.get("content")
        return str(token) if token else None
    token = chunk.get("content") or chunk.get("text")
    return str(token) if token else None


class CustomAPIAdapter(ModelAdapter):
    """
    Connects to any REST endpoint that accepts a JSON POST body.

    Ships with OpenAI-like defaults for request serialisation, response
    parsing, and SSE streaming. Override any of the three callables to
    adapt to a different payload shape without subclassing.

    Basic usage (OpenAI-compatible default):
        adapter = CustomAPIAdapter(
            model="my-model",
            base_url="https://my-api.example.com",
            api_key="secret",
        )
        response = await adapter.complete(request)

    Custom shape:
        def build(req: CompletionRequest) -> dict:
            return {"prompt": req.messages[-1]["content"], "max_new_tokens": req.max_tokens}

        def parse(data: dict) -> CompletionResponse:
            return CompletionResponse(content=data["generated_text"])

        adapter = CustomAPIAdapter(
            model="my-model",
            base_url="https://my-api.example.com",
            chat_path="/generate",
            request_builder=build,
            response_parser=parse,
        )
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        chat_path: str = "/chat/completions",
        stream_path: str | None = None,
        health_path: str | None = None,
        timeout: float = 120.0,
        request_builder: _RequestBuilder | None = None,
        response_parser: _ResponseParser | None = None,
        stream_chunk_parser: _StreamChunkParser | None = None,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._chat_path = "/" + chat_path.lstrip("/")
        self._stream_path = "/" + (stream_path or chat_path).lstrip("/")
        self._health_path = "/" + health_path.lstrip("/") if health_path else None

        resolved_key = api_key or os.getenv("CUSTOM_API_KEY")
        merged_headers: dict[str, str] = {"Content-Type": "application/json", **(headers or {})}
        if resolved_key:
            merged_headers.setdefault("Authorization", f"Bearer {resolved_key}")

        self._request_builder: _RequestBuilder = request_builder or _openai_like_request_builder(model)
        self._response_parser: _ResponseParser = response_parser or _openai_like_response_parser
        self._stream_chunk_parser: _StreamChunkParser = stream_chunk_parser or _sse_chunk_parser
        self._client = httpx.AsyncClient(timeout=timeout, headers=merged_headers)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        body = self._request_builder(request)
        response = await self._client.post(f"{self._base_url}{self._chat_path}", json=body)
        response.raise_for_status()
        return self._response_parser(response.json())

    async def stream(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        body = {**self._request_builder(request), "stream": True}
        async with self._client.stream("POST", f"{self._base_url}{self._stream_path}", json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if token := self._stream_chunk_parser(line):
                    yield token

    async def health_check(self) -> ModelHealth:
        try:
            if self._health_path:
                resp = await self._client.get(f"{self._base_url}{self._health_path}")
                resp.raise_for_status()
                return ModelHealth(healthy=True, model_id=self.model)
            resp = await self._client.head(self._base_url)
            ok = resp.is_success or resp.status_code < 500
            return ModelHealth(
                healthy=ok,
                model_id=self.model,
                message="" if ok else f"HTTP {resp.status_code}",
            )
        except httpx.ConnectError as exc:
            return ModelHealth(healthy=False, model_id=self.model, message=f"Connection error: {exc}")
        except Exception as exc:
            return ModelHealth(healthy=False, model_id=self.model, message=str(exc))
