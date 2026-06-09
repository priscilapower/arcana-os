"""Tests for OllamaAdapter."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.models.adapters.base import CompletionRequest
from arcana.models.adapters.ollama import OllamaAdapter


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        system=kw.get("system", "You are helpful."),
        messages=kw.get("messages", [{"role": "user", "content": "Hello"}]),
        temperature=kw.get("temperature", 0.7),
    )


def _ok_response(content: str = "", input_tokens: int = 0, output_tokens: int = 0) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "message": {"content": content},
        "prompt_eval_count": input_tokens,
        "eval_count": output_tokens,
    }
    return resp


@pytest.fixture
def mock_http():
    client = MagicMock()
    client.post = AsyncMock()
    client.get = AsyncMock()
    return client


@pytest.fixture
def adapter(mock_http):
    with patch("arcana.models.adapters.ollama.httpx.AsyncClient", return_value=mock_http):
        return OllamaAdapter(model="llama3")


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_maps_response_fields(adapter, mock_http):
    mock_http.post.return_value = _ok_response("Hello!", input_tokens=8, output_tokens=3)

    result = await adapter.complete(_req())

    assert result.content == "Hello!"
    assert result.input_tokens == 8
    assert result.output_tokens == 3


@pytest.mark.asyncio
async def test_complete_sends_system_as_first_message(adapter, mock_http):
    mock_http.post.return_value = _ok_response()

    await adapter.complete(_req(system="Be concise."))

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["messages"][0] == {"role": "system", "content": "Be concise."}


@pytest.mark.asyncio
async def test_complete_omits_system_message_when_empty(adapter, mock_http):
    mock_http.post.return_value = _ok_response()

    await adapter.complete(_req(system=""))

    payload = mock_http.post.call_args.kwargs["json"]
    assert all(m["role"] != "system" for m in payload["messages"])


@pytest.mark.asyncio
async def test_complete_passes_multi_turn_messages(adapter, mock_http):
    mock_http.post.return_value = _ok_response()
    conversation = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Tell me more"},
    ]
    await adapter.complete(_req(system="", messages=conversation))

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["messages"] == conversation


@pytest.mark.asyncio
async def test_complete_passes_temperature_in_options(adapter, mock_http):
    mock_http.post.return_value = _ok_response()

    await adapter.complete(_req(temperature=0.42))

    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["options"]["temperature"] == 0.42
    assert payload["stream"] is False


@pytest.mark.asyncio
async def test_complete_posts_to_correct_endpoint(adapter, mock_http):
    mock_http.post.return_value = _ok_response()

    await adapter.complete(_req())

    url = mock_http.post.call_args.args[0]
    assert url == "http://localhost:11434/api/chat"


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


async def _alines(lines):
    for line in lines:
        yield line


def _stream_cm(lines):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.aiter_lines.return_value = _alines(lines)

    cm = AsyncMock()
    cm.__aenter__.return_value = resp
    return cm


@pytest.mark.asyncio
async def test_stream_yields_content_chunks(adapter, mock_http):
    lines = [
        json.dumps({"message": {"content": "Hello"}, "done": False}),
        json.dumps({"message": {"content": " world"}, "done": False}),
        json.dumps({"message": {}, "done": True}),
    ]
    mock_http.stream.return_value = _stream_cm(lines)

    collected = [chunk async for chunk in adapter.stream(_req())]

    assert [c.text for c in collected if c.text] == ["Hello", " world"]
    # Final chunk from the done event carries usage (zeros here since mock omits counts)
    assert collected[-1].input_tokens == 0
    assert collected[-1].output_tokens == 0


@pytest.mark.asyncio
async def test_stream_stops_on_done_flag(adapter, mock_http):
    lines = [
        json.dumps({"message": {"content": "only"}, "done": False}),
        json.dumps({"message": {}, "done": True}),
        json.dumps({"message": {"content": "ignored"}, "done": False}),
    ]
    mock_http.stream.return_value = _stream_cm(lines)

    collected = [chunk async for chunk in adapter.stream(_req())]

    assert [c.text for c in collected if c.text] == ["only"]


@pytest.mark.asyncio
async def test_stream_skips_empty_content(adapter, mock_http):
    lines = [
        json.dumps({"message": {"content": ""}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": False}),
        json.dumps({"message": {}, "done": True}),
    ]
    mock_http.stream.return_value = _stream_cm(lines)

    collected = [chunk async for chunk in adapter.stream(_req())]

    # Only the final usage chunk is yielded (no content chunks)
    assert [c.text for c in collected if c.text] == []
    assert len(collected) == 1  # just the done/usage chunk


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_model_available(adapter, mock_http):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"models": [{"name": "llama3"}, {"name": "mistral"}]}
    mock_http.get.return_value = resp

    health = await adapter.health_check()

    assert health.healthy is True
    assert health.model_id == "llama3"
    assert health.message == ""


@pytest.mark.asyncio
async def test_health_check_model_not_in_list(adapter, mock_http):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"models": [{"name": "mistral"}]}
    mock_http.get.return_value = resp

    health = await adapter.health_check()

    assert health.healthy is False
    assert "mistral" in health.message


@pytest.mark.asyncio
async def test_health_check_connection_error(adapter, mock_http):
    mock_http.get.side_effect = Exception("Connection refused")

    health = await adapter.health_check()

    assert health.healthy is False
    assert "Connection refused" in health.message
