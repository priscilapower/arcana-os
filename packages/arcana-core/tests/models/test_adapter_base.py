"""Tests for ModelAdapter base-class guard logic."""

import pytest

from arcana.models.adapters.base import CompletionRequest, ModelAdapter, ModelHealth
from arcana.models.errors import ModelBadRequestError


class _StubAdapter(ModelAdapter):
    """Minimal adapter with no tool support (uses base default supports_tools=False)."""

    async def complete(self, request: CompletionRequest):
        self._guard_tools(request)
        return None  # type: ignore[return-value]

    async def stream(self, request: CompletionRequest):  # type: ignore[override]
        return
        yield  # make it a generator

    async def health_check(self):
        return ModelHealth(healthy=True, model_id="stub")


def _req_with_tools() -> CompletionRequest:
    return CompletionRequest(
        system="",
        messages=[{"role": "user", "content": "Hi"}],
        tools=[{"name": "search", "description": "find things", "input_schema": {"type": "object"}}],  # type: ignore[arg-type]
    )


def _req_no_tools() -> CompletionRequest:
    return CompletionRequest(system="", messages=[{"role": "user", "content": "Hi"}])


def test_adapter_raises_on_unsupported_tools():
    """_guard_tools raises ModelBadRequestError when tools are requested but not supported."""
    adapter = _StubAdapter()
    assert adapter.supports_tools is False

    with pytest.raises(ModelBadRequestError, match="does not support tool calls"):
        adapter._guard_tools(_req_with_tools())


def test_adapter_guard_passes_when_no_tools():
    """_guard_tools does not raise when request carries no tools."""
    adapter = _StubAdapter()
    adapter._guard_tools(_req_no_tools())  # must not raise


@pytest.mark.asyncio
async def test_adapter_raises_via_complete_on_unsupported_tools():
    """complete() raises ModelBadRequestError when supports_tools is False and tools are passed."""
    adapter = _StubAdapter()

    with pytest.raises(ModelBadRequestError):
        await adapter.complete(_req_with_tools())
