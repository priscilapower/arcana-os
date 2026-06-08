"""Tests for CustomAPIAdapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arcana.models.adapters.base import CompletionRequest, CompletionResponse
from arcana.models.adapters.custom_api import (
    CustomAPIAdapter,
    _openai_like_request_builder,
    _openai_like_response_parser,
    _sse_chunk_parser,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_http():
    client = MagicMock()
    client.post = AsyncMock()
    client.get = AsyncMock()
    client.head = AsyncMock()
    return client


@pytest.fixture
def adapter(mock_http):
    with patch("arcana.models.adapters.custom_api.httpx.AsyncClient", return_value=mock_http):
        return CustomAPIAdapter(model="m", base_url="http://host", api_key="k")


def _stream_cm(lines: list[str]) -> AsyncMock:
    """Build an async context manager that yields SSE lines from the response."""

    async def _aiter():
        for line in lines:
            yield line

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.aiter_lines.return_value = _aiter()
    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    return cm


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        system=kw.get("system", "You are helpful."),
        messages=kw.get("messages", [{"role": "user", "content": "Hello"}]),
        temperature=kw.get("temperature", 0.7),
        max_tokens=kw.get("max_tokens", 1024),
    )


def _openai_response(content: str = "Hi!", finish_reason: str = "stop") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10},
    }


# ---------------------------------------------------------------------------
# _openai_like_request_builder
# ---------------------------------------------------------------------------


def test_request_builder_includes_system_as_first_message():
    build = _openai_like_request_builder("my-model")
    body = build(_req(system="Be brief."))
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}


def test_request_builder_omits_system_when_empty():
    build = _openai_like_request_builder("my-model")
    body = build(_req(system=""))
    assert all(m["role"] != "system" for m in body["messages"])


def test_request_builder_sets_model_and_params():
    build = _openai_like_request_builder("gpt-custom")
    body = build(_req(temperature=0.3, max_tokens=512))
    assert body["model"] == "gpt-custom"
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 512


# ---------------------------------------------------------------------------
# _openai_like_response_parser
# ---------------------------------------------------------------------------


def test_response_parser_openai_shape():
    result = _openai_like_response_parser(_openai_response("Hello world"))
    assert result.content == "Hello world"
    assert result.input_tokens == 5
    assert result.output_tokens == 10
    assert result.stop_reason == "end_turn"


def test_response_parser_maps_stop_reason_length():
    result = _openai_like_response_parser(_openai_response(finish_reason="length"))
    assert result.stop_reason == "max_tokens"


def test_response_parser_maps_stop_reason_tool_calls():
    result = _openai_like_response_parser(_openai_response(finish_reason="tool_calls"))
    assert result.stop_reason == "tool_use"


def test_response_parser_flat_content_field():
    result = _openai_like_response_parser({"content": "flat response"})
    assert result.content == "flat response"


def test_response_parser_flat_text_field():
    result = _openai_like_response_parser({"text": "text response"})
    assert result.content == "text response"


def test_response_parser_flat_message_field():
    result = _openai_like_response_parser({"message": "msg response"})
    assert result.content == "msg response"


# ---------------------------------------------------------------------------
# _sse_chunk_parser
# ---------------------------------------------------------------------------


def test_sse_chunk_parser_openai_delta():
    line = 'data: {"choices": [{"delta": {"content": "hello"}}]}'
    assert _sse_chunk_parser(line) == "hello"


def test_sse_chunk_parser_done_sentinel():
    assert _sse_chunk_parser("data: [DONE]") is None


def test_sse_chunk_parser_non_data_line():
    assert _sse_chunk_parser(": keep-alive") is None
    assert _sse_chunk_parser("") is None


def test_sse_chunk_parser_flat_content():
    line = 'data: {"content": "token"}'
    assert _sse_chunk_parser(line) == "token"


def test_sse_chunk_parser_flat_text():
    line = 'data: {"text": "tok"}'
    assert _sse_chunk_parser(line) == "tok"


def test_sse_chunk_parser_malformed_json():
    assert _sse_chunk_parser("data: not-json") is None


def test_sse_chunk_parser_empty_delta_content():
    line = 'data: {"choices": [{"delta": {}}]}'
    assert _sse_chunk_parser(line) is None


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_defaults():
    adapter = CustomAPIAdapter(model="my-model", base_url="http://localhost:8080")
    assert adapter.model == "my-model"
    assert adapter._base_url == "http://localhost:8080"
    assert adapter._chat_path == "/chat/completions"
    assert adapter._stream_path == "/chat/completions"
    assert adapter._health_path is None


def test_trailing_slash_stripped():
    adapter = CustomAPIAdapter(model="m", base_url="http://localhost:8080/")
    assert adapter._base_url == "http://localhost:8080"


def test_custom_paths():
    adapter = CustomAPIAdapter(
        model="m",
        base_url="http://host",
        chat_path="generate",
        stream_path="generate/stream",
        health_path="health",
    )
    assert adapter._chat_path == "/generate"
    assert adapter._stream_path == "/generate/stream"
    assert adapter._health_path == "/health"


def test_api_key_sets_authorization_header():
    adapter = CustomAPIAdapter(model="m", base_url="http://host", api_key="my-secret")
    assert adapter._client.headers["authorization"] == "Bearer my-secret"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("CUSTOM_API_KEY", "env-secret")
    adapter = CustomAPIAdapter(model="m", base_url="http://host")
    assert adapter._client.headers["authorization"] == "Bearer env-secret"


def test_extra_headers_merged():
    adapter = CustomAPIAdapter(
        model="m", base_url="http://host", headers={"X-Tenant": "acme", "Authorization": "Token custom"}
    )
    assert adapter._client.headers["x-tenant"] == "acme"
    assert adapter._client.headers["authorization"] == "Token custom"


def test_explicit_api_key_does_not_override_custom_auth_header():
    """Custom Authorization header takes precedence over api_key."""
    adapter = CustomAPIAdapter(
        model="m", base_url="http://host", api_key="key", headers={"Authorization": "Token custom"}
    )
    assert adapter._client.headers["authorization"] == "Token custom"


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_posts_to_chat_path(adapter, mock_http):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = _openai_response("Hi!")
    mock_http.post.return_value = mock_response

    result = await adapter.complete(_req())

    url = mock_http.post.call_args.args[0]
    assert url == "http://host/chat/completions"
    assert result.content == "Hi!"
    assert result.input_tokens == 5
    assert result.output_tokens == 10


@pytest.mark.asyncio
async def test_complete_uses_custom_request_builder(mock_http):
    captured: list[dict] = []

    def my_builder(req: CompletionRequest) -> dict:
        body = {"prompt": req.messages[-1]["content"]}
        captured.append(body)
        return body

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"content": "ok"}
    mock_http.post.return_value = mock_response

    with patch("arcana.models.adapters.custom_api.httpx.AsyncClient", return_value=mock_http):
        custom = CustomAPIAdapter(model="m", base_url="http://host", request_builder=my_builder)
    await custom.complete(_req(messages=[{"role": "user", "content": "Test prompt"}]))

    assert captured[0] == {"prompt": "Test prompt"}


@pytest.mark.asyncio
async def test_complete_uses_custom_response_parser(mock_http):
    def my_parser(data: dict) -> CompletionResponse:
        return CompletionResponse(content=data["generated_text"])

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"generated_text": "custom output"}
    mock_http.post.return_value = mock_response

    with patch("arcana.models.adapters.custom_api.httpx.AsyncClient", return_value=mock_http):
        custom = CustomAPIAdapter(model="m", base_url="http://host", response_parser=my_parser)
    result = await custom.complete(_req())

    assert result.content == "custom output"


@pytest.mark.asyncio
async def test_complete_raises_on_http_error(adapter, mock_http):
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    mock_http.post.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.complete(_req())


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_yields_tokens(adapter, mock_http):
    lines = [
        'data: {"choices": [{"delta": {"content": "Hello"}}]}',
        'data: {"choices": [{"delta": {"content": " world"}}]}',
        "data: [DONE]",
    ]
    mock_http.stream.return_value = _stream_cm(lines)

    tokens = [t async for t in adapter.stream(_req())]

    assert tokens == ["Hello", " world"]


@pytest.mark.asyncio
async def test_stream_adds_stream_flag_to_body(adapter, mock_http):
    mock_http.stream.return_value = _stream_cm([])

    _ = [t async for t in adapter.stream(_req())]

    body = mock_http.stream.call_args.kwargs["json"]
    assert body["stream"] is True


@pytest.mark.asyncio
async def test_stream_uses_custom_chunk_parser(mock_http):
    def my_parser(line: str) -> str | None:
        if line.startswith("TOKEN:"):
            return line[6:]
        return None

    mock_http.stream.return_value = _stream_cm(["TOKEN:hello", "TOKEN:world"])

    with patch("arcana.models.adapters.custom_api.httpx.AsyncClient", return_value=mock_http):
        custom = CustomAPIAdapter(model="m", base_url="http://host", stream_chunk_parser=my_parser)
    tokens = [t async for t in custom.stream(_req())]

    assert tokens == ["hello", "world"]


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_with_health_path_success(mock_http):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_http.get.return_value = mock_resp

    with patch("arcana.models.adapters.custom_api.httpx.AsyncClient", return_value=mock_http):
        health_adapter = CustomAPIAdapter(model="m", base_url="http://host", health_path="/health")
    health = await health_adapter.health_check()

    assert health.healthy is True
    assert health.model_id == "m"


@pytest.mark.asyncio
async def test_health_check_with_health_path_error(mock_http):
    mock_http.get.side_effect = httpx.ConnectError("refused")

    with patch("arcana.models.adapters.custom_api.httpx.AsyncClient", return_value=mock_http):
        health_adapter = CustomAPIAdapter(model="m", base_url="http://host", health_path="/health")
    health = await health_adapter.health_check()

    assert health.healthy is False
    assert "Connection error" in health.message


@pytest.mark.asyncio
async def test_health_check_no_health_path_head_success(adapter, mock_http):
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_http.head.return_value = mock_resp

    health = await adapter.health_check()

    assert health.healthy is True
    assert health.message == ""


@pytest.mark.asyncio
async def test_health_check_no_health_path_head_server_error(adapter, mock_http):
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 503
    mock_http.head.return_value = mock_resp

    health = await adapter.health_check()

    assert health.healthy is False
    assert "503" in health.message


@pytest.mark.asyncio
async def test_health_check_generic_exception(adapter, mock_http):
    mock_http.head.side_effect = RuntimeError("boom")

    health = await adapter.health_check()

    assert health.healthy is False
    assert "boom" in health.message
