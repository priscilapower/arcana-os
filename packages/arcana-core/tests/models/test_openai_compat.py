"""Tests for OpenAICompatAdapter."""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
)

from arcana.models.adapters.base import CompletionRequest
from arcana.models.adapters.openai_compat import OpenAICompatAdapter

_MODULE = "arcana.models.adapters.openai_compat"


def test_import_error_when_openai_missing():
    saved = sys.modules.pop(_MODULE, None)
    try:
        with patch.dict(sys.modules, {"openai": None}):
            with pytest.raises(ImportError, match="Install arcana-core"):
                importlib.import_module(_MODULE)
    finally:
        if saved is not None:
            sys.modules[_MODULE] = saved


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        system=kw.get("system", "You are helpful."),
        messages=kw.get("messages", [{"role": "user", "content": "Hello"}]),
        temperature=kw.get("temperature", 0.7),
        max_tokens=kw.get("max_tokens", 1024),
        tools=kw.get("tools", None),
    )


def _choice(content: str = "response", finish_reason: str = "stop", tool_calls=None) -> MagicMock:
    choice = MagicMock()
    choice.message = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    choice.finish_reason = finish_reason
    return choice


def _completion(
    content: str = "response",
    finish_reason: str = "stop",
    tool_calls=None,
    prompt_tokens: int = 5,
    completion_tokens: int = 10,
) -> MagicMock:
    resp = MagicMock()
    resp.choices = [_choice(content, finish_reason, tool_calls)]
    resp.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return resp


def _mock_openai_client() -> MagicMock:
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion())
    client.models = MagicMock()
    client.models.list = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Construction / API key resolution
# ---------------------------------------------------------------------------


def test_defaults():
    adapter = OpenAICompatAdapter(model="llama3")
    assert adapter.model == "llama3"
    assert adapter._base_url == "http://localhost:1234/v1"
    assert adapter._client is None


def test_trailing_slash_stripped():
    adapter = OpenAICompatAdapter(model="x", base_url="http://localhost:1234/v1/")
    assert adapter._base_url == "http://localhost:1234/v1"


def test_api_key_explicit_arg(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sdk = _mock_openai_client()
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk) as MockCls:
        adapter = OpenAICompatAdapter(model="x", api_key="my-key")
        adapter._get_client()
    MockCls.assert_called_once_with(api_key="my-key", base_url="http://localhost:1234/v1", timeout=120.0)


def test_api_key_from_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    sdk = _mock_openai_client()
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk) as MockCls:
        adapter = OpenAICompatAdapter(model="x")
        adapter._get_client()
    MockCls.assert_called_once_with(api_key="env-key", base_url="http://localhost:1234/v1", timeout=120.0)


def test_api_key_falls_back_to_not_needed(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sdk = _mock_openai_client()
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk) as MockCls:
        adapter = OpenAICompatAdapter(model="x")
        adapter._get_client()
    MockCls.assert_called_once_with(api_key="not-needed", base_url="http://localhost:1234/v1", timeout=120.0)


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_maps_response_fields():
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _completion("Hi!", "stop", prompt_tokens=7, completion_tokens=2)

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        result = await adapter.complete(_req())

    assert result.content == "Hi!"
    assert result.input_tokens == 7
    assert result.output_tokens == 2
    assert result.stop_reason == "end_turn"
    assert result.tool_calls is None


@pytest.mark.asyncio
async def test_complete_includes_system_as_message():
    sdk = _mock_openai_client()
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(system="Be brief."))

    messages = sdk.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "Be brief."}


@pytest.mark.asyncio
async def test_complete_omits_system_message_when_empty():
    sdk = _mock_openai_client()
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(system=""))

    messages = sdk.chat.completions.create.call_args.kwargs["messages"]
    assert all(m["role"] != "system" for m in messages)


@pytest.mark.asyncio
async def test_complete_preserves_multi_turn_message_order():
    sdk = _mock_openai_client()
    conversation = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "How are you?"},
    ]
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(system="", messages=conversation))

    messages = sdk.chat.completions.create.call_args.kwargs["messages"]
    assert len(messages) == 3
    assert messages[0] == {"role": "user", "content": "Hello"}
    assert messages[1] == {"role": "assistant", "content": "Hi there"}
    assert messages[2] == {"role": "user", "content": "How are you?"}


@pytest.mark.asyncio
async def test_complete_maps_unknown_role_as_user():
    sdk = _mock_openai_client()
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(system="", messages=[{"role": "custom", "content": "Hi"}]))

    messages = sdk.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hi"


@pytest.mark.asyncio
async def test_complete_maps_stop_reason_length():
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _completion(finish_reason="length")

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        result = await adapter.complete(_req())

    assert result.stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_complete_maps_stop_reason_tool_calls():
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _completion(finish_reason="tool_calls")

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        result = await adapter.complete(_req())

    assert result.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_complete_maps_tool_calls():
    tc = MagicMock(spec=ChatCompletionMessageFunctionToolCall)
    tc.id = "call_abc"
    tc.type = "function"
    tc.function = MagicMock()
    tc.function.name = "get_weather"
    tc.function.arguments = '{"city": "NYC"}'

    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _completion(tool_calls=[tc])

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        result = await adapter.complete(_req())

    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["id"] == "call_abc"
    assert result.tool_calls[0]["function"]["name"] == "get_weather"
    assert result.tool_calls[0]["function"]["arguments"] == '{"city": "NYC"}'


@pytest.mark.asyncio
async def test_complete_with_tools_sets_tool_choice():
    sdk = _mock_openai_client()
    tools = [{"name": "search", "description": "Search the web", "input_schema": {"type": "object", "properties": {}}}]

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(tools=tools))

    call_kwargs = sdk.chat.completions.create.call_args.kwargs
    expected_tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search the web",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert call_kwargs["tools"] == expected_tools
    assert call_kwargs["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_complete_tools_converted_to_openai_function_format():
    sdk = _mock_openai_client()
    tools = [
        {
            "name": "add",
            "description": "Add two numbers",
            "input_schema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
        },
        {"name": "sub", "description": "Subtract", "input_schema": {"type": "object", "properties": {}}},
    ]
    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(tools=tools))

    sent_tools = sdk.chat.completions.create.call_args.kwargs["tools"]
    assert len(sent_tools) == 2
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == "add"
    assert sent_tools[0]["function"]["description"] == "Add two numbers"
    assert sent_tools[0]["function"]["parameters"] == tools[0]["input_schema"]
    assert sent_tools[1]["function"]["name"] == "sub"


@pytest.mark.asyncio
async def test_complete_tool_calls_return_typed_structure():
    tc = MagicMock(spec=ChatCompletionMessageFunctionToolCall)
    tc.id = "call_xyz"
    tc.type = "function"
    tc.function = MagicMock()
    tc.function.name = "lookup"
    tc.function.arguments = '{"q": "arcana"}'

    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _completion(tool_calls=[tc])

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        result = await adapter.complete(_req())

    assert result.tool_calls is not None
    call = result.tool_calls[0]
    assert call["id"] == "call_xyz"
    assert call["type"] == "function"
    assert call["function"]["name"] == "lookup"
    assert call["function"]["arguments"] == '{"q": "arcana"}'


@pytest.mark.asyncio
async def test_complete_custom_tool_call_type_is_excluded():
    """ChatCompletionMessageCustomToolCall items (type != "function") are filtered out."""
    from openai.types.chat.chat_completion_message_custom_tool_call import ChatCompletionMessageCustomToolCall

    custom_tc = MagicMock(spec=ChatCompletionMessageCustomToolCall)
    custom_tc.id = "call_custom"
    custom_tc.type = "custom"

    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _completion(tool_calls=[custom_tc])

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        result = await adapter.complete(_req())

    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_complete_without_tools_omits_tool_choice():
    sdk = _mock_openai_client()

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        await adapter.complete(_req(tools=None))

    call_kwargs = sdk.chat.completions.create.call_args.kwargs
    assert "tool_choice" not in call_kwargs


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


class _MockStream:
    """Minimal mock for openai.AsyncStream: iterable + close()."""

    def __init__(self, *contents, usage=None):
        self._contents = contents
        self._usage = usage
        self.close = AsyncMock()

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for content in self._contents:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = content
            chunk.usage = None
            yield chunk
        if self._usage is not None:
            final = MagicMock()
            final.choices = []
            final.usage = self._usage
            yield final


@pytest.mark.asyncio
async def test_stream_yields_delta_content():
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _MockStream("Hello", " world")

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        collected = [chunk async for chunk in adapter.stream(_req())]

    assert [c.text for c in collected if c.text] == ["Hello", " world"]


@pytest.mark.asyncio
async def test_stream_skips_none_content():
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _MockStream(None, "hi", None)

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        collected = [chunk async for chunk in adapter.stream(_req())]

    assert [c.text for c in collected if c.text] == ["hi"]


@pytest.mark.asyncio
async def test_stream_passes_stream_true():
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _MockStream()

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        _ = [chunk async for chunk in adapter.stream(_req())]

    call_kwargs = sdk.chat.completions.create.call_args.kwargs
    assert call_kwargs["stream"] is True


@pytest.mark.asyncio
async def test_stream_propagates_usage_from_final_chunk():
    usage = MagicMock(prompt_tokens=15, completion_tokens=7)
    sdk = _mock_openai_client()
    sdk.chat.completions.create.return_value = _MockStream("hello", usage=usage)

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        collected = [chunk async for chunk in adapter.stream(_req())]

    usage_chunk = next(c for c in collected if c.input_tokens or c.output_tokens)
    assert usage_chunk.input_tokens == 15
    assert usage_chunk.output_tokens == 7


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_model_available():
    sdk = _mock_openai_client()
    page = MagicMock()
    page.data = [MagicMock(id="llama3"), MagicMock(id="mistral")]
    sdk.models.list.return_value = page

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        health = await adapter.health_check()

    assert health.healthy is True
    assert health.message == ""


@pytest.mark.asyncio
async def test_health_check_model_not_in_list():
    sdk = _mock_openai_client()
    page = MagicMock()
    page.data = [MagicMock(id="mistral")]
    sdk.models.list.return_value = page

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        health = await adapter.health_check()

    assert health.healthy is False
    assert "mistral" in health.message


@pytest.mark.asyncio
async def test_health_check_api_connection_error():
    sdk = _mock_openai_client()
    sdk.models.list.side_effect = openai.APIConnectionError(request=httpx.Request("GET", "http://localhost"))

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        health = await adapter.health_check()

    assert health.healthy is False
    assert "Connection error" in health.message


@pytest.mark.asyncio
async def test_health_check_generic_exception():
    sdk = _mock_openai_client()
    sdk.models.list.side_effect = RuntimeError("Something went wrong")

    with patch("arcana.models.adapters.openai_compat.AsyncOpenAI", return_value=sdk):
        adapter = OpenAICompatAdapter(model="llama3", api_key="k")
        health = await adapter.health_check()

    assert health.healthy is False
    assert "Something went wrong" in health.message
