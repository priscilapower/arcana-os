"""Tests for AnthropicAdapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import TextBlock

from arcana.models.adapters.anthropic import AnthropicAdapter
from arcana.models.adapters.base import CompletionRequest


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        system=kw.get("system", "You are helpful."),
        messages=kw.get("messages", [{"role": "user", "content": "Hello"}]),
        temperature=kw.get("temperature", 0.7),
        max_tokens=kw.get("max_tokens", 1024),
    )


def _text_block(text: str) -> MagicMock:
    block = MagicMock(spec=TextBlock)
    block.text = text
    return block


def _mock_sdk_client(
    text: str = "response", input_tokens: int = 5, output_tokens: int = 10, stop_reason: str | None = "end_turn"
) -> MagicMock:
    """Build a minimal mock of anthropic.AsyncAnthropic."""
    client = MagicMock()
    client.messages = MagicMock()

    msg = MagicMock()
    msg.content = [_text_block(text)]
    msg.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
    msg.stop_reason = stop_reason
    client.messages.create = AsyncMock(return_value=msg)

    return client


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def test_resolve_key_reads_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-123")
    adapter = AnthropicAdapter()
    assert adapter._resolve_key() == "env-key-123"


def test_resolve_key_falls_back_to_keyring(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("arcana.models.adapters.anthropic.keyring.get_password", return_value="keyring-key"):
        adapter = AnthropicAdapter()
        assert adapter._resolve_key() == "keyring-key"


def test_resolve_key_raises_when_no_key_found(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("arcana.models.adapters.anthropic.keyring.get_password", return_value=None):
        adapter = AnthropicAdapter()
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            adapter._resolve_key()


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_maps_response_fields():
    sdk = _mock_sdk_client(text="Hello!", input_tokens=8, output_tokens=3, stop_reason="end_turn")
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        result = await adapter.complete(_req())

    assert result.content == "Hello!"
    assert result.input_tokens == 8
    assert result.output_tokens == 3
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_complete_passes_system_and_messages():
    sdk = _mock_sdk_client()
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        await adapter.complete(_req(system="Be brief.", messages=[{"role": "user", "content": "Hi"}]))

    call_kwargs = sdk.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "Be brief."
    assert call_kwargs["messages"] == [{"role": "user", "content": "Hi"}]


@pytest.mark.asyncio
async def test_complete_passes_temperature_and_max_tokens():
    sdk = _mock_sdk_client()
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(model="claude-haiku-4-5-20251001", api_key="test-key")
        await adapter.complete(_req(temperature=0.2, max_tokens=512))

    call_kwargs = sdk.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 512
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_complete_passes_multi_turn_messages():
    sdk = _mock_sdk_client()
    conversation = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
        {"role": "user", "content": "Tell me more"},
    ]
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        await adapter.complete(_req(system="", messages=conversation))

    call_kwargs = sdk.messages.create.call_args.kwargs
    msgs = call_kwargs["messages"]
    assert len(msgs) == 3
    assert msgs[0] == {"role": "user", "content": "Hello"}
    assert msgs[1] == {"role": "assistant", "content": "Hi"}
    assert msgs[2] == {"role": "user", "content": "Tell me more"}


@pytest.mark.asyncio
async def test_complete_filters_unknown_role():
    sdk = _mock_sdk_client()
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        await adapter.complete(
            _req(
                messages=[
                    {"role": "user", "content": "Hello"},
                    {"role": "tool", "content": "result"},
                    {"role": "user", "content": "Thanks"},
                ]
            )
        )

    msgs = sdk.messages.create.call_args.kwargs["messages"]
    assert len(msgs) == 2
    assert all(m["role"] in ("user", "assistant", "system") for m in msgs)


@pytest.mark.asyncio
async def test_complete_returns_empty_string_when_no_text_block():
    sdk = _mock_sdk_client()
    sdk.messages.create.return_value.content = [MagicMock()]  # no TextBlock spec
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        result = await adapter.complete(_req())

    assert result.content == ""


@pytest.mark.asyncio
async def test_complete_extracts_first_text_block_from_mixed_content():
    sdk = _mock_sdk_client()
    sdk.messages.create.return_value.content = [
        MagicMock(),  # non-TextBlock (tool_use, thinking, etc.)
        _text_block("found"),
        _text_block("second"),
    ]
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        result = await adapter.complete(_req())

    assert result.content == "found"


@pytest.mark.asyncio
async def test_complete_stop_reason_none_becomes_end_turn():
    sdk = _mock_sdk_client(stop_reason=None)
    sdk.messages.create.return_value.stop_reason = None
    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        result = await adapter.complete(_req())

    assert result.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


async def _text_chunks(*texts):
    for t in texts:
        yield t


@pytest.mark.asyncio
async def test_stream_yields_text_chunks():
    sdk = MagicMock()
    sdk.messages = MagicMock()

    stream_inner = MagicMock()
    stream_inner.text_stream = _text_chunks("Hello", " world")

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = stream_inner
    sdk.messages.stream.return_value = stream_cm

    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(api_key="test-key")
        collected = [chunk async for chunk in adapter.stream(_req())]

    assert collected == ["Hello", " world"]


@pytest.mark.asyncio
async def test_stream_passes_correct_kwargs():
    sdk = MagicMock()
    sdk.messages = MagicMock()

    stream_inner = MagicMock()
    stream_inner.text_stream = _text_chunks()

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = stream_inner
    sdk.messages.stream.return_value = stream_cm

    with patch("arcana.models.adapters.anthropic.AsyncAnthropic", return_value=sdk):
        adapter = AnthropicAdapter(model="claude-sonnet-4-6", api_key="test-key")
        _ = [chunk async for chunk in adapter.stream(_req(system="Sys", temperature=0.5, max_tokens=256))]

    call_kwargs = sdk.messages.stream.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert call_kwargs["system"] == "Sys"
    assert call_kwargs["temperature"] == 0.5
    assert call_kwargs["max_tokens"] == 256


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_healthy_when_key_available(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "valid-key")
    adapter = AnthropicAdapter()

    health = await adapter.health_check()

    assert health.healthy is True
    assert health.message == ""


@pytest.mark.asyncio
async def test_health_check_unhealthy_when_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("arcana.models.adapters.anthropic.keyring.get_password", return_value=None):
        adapter = AnthropicAdapter()
        health = await adapter.health_check()

    assert health.healthy is False
    assert "ANTHROPIC_API_KEY" in health.message
