"""Tests for ModelGateway, RetryPolicy, PricingTable, and ConnectionStore."""

from contextlib import aclosing
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.models.adapters.base import CompletionRequest, CompletionResponse, ModelChunk, ModelHealth
from arcana.models.connection_store import ConnectionStore
from arcana.models.errors import (
    ModelAuthError,
    ModelBadRequestError,
    ModelTransientError,
    ModelUnavailableError,
)
from arcana.models.gateway import (
    ModelGateway,
    ProviderEntry,
    ProviderRegistry,
    RetryPolicy,
    _cache_key,
)
from arcana.models.pricing import CostEvent, PricingTable, Usage
from arcana.types.model import ModelConnection, ModelProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req(**kw) -> CompletionRequest:
    return CompletionRequest(
        system=kw.get("system", "You are helpful."),
        messages=kw.get("messages", [{"role": "user", "content": "hi"}]),
        temperature=kw.get("temperature", 0.7),
    )


def _ok_response(content: str = "ok", *, input_tokens: int = 10, output_tokens: int = 5) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _make_adapter(*, response: CompletionResponse | None = None, side_effect=None) -> MagicMock:
    adapter = MagicMock()
    adapter.connect = AsyncMock()
    adapter.aclose = AsyncMock()
    adapter.health_check = AsyncMock(return_value=ModelHealth(healthy=True, model_id="test"))
    if side_effect is not None:
        adapter.complete = AsyncMock(side_effect=side_effect)
    else:
        adapter.complete = AsyncMock(return_value=response or _ok_response())
    return adapter


def _make_store(*, api_key: str | None = None) -> MagicMock:
    store = MagicMock(spec=ConnectionStore)
    store.get_by_provider.return_value = None
    store.get_by_name.return_value = None
    store.get_api_key.return_value = api_key
    return store


def _make_registry(adapter: MagicMock) -> ProviderRegistry:
    factory = MagicMock(return_value=adapter)
    entry = ProviderEntry(factory=factory, default_endpoint="http://localhost:11434")
    return ProviderRegistry({"ollama": entry, "anthropic": entry})


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


def test_retry_policy_honours_retry_after():
    policy = RetryPolicy()
    delay = policy.backoff(0, retry_after=5.0)
    assert delay == 5.0


def test_retry_policy_backoff_within_cap():
    policy = RetryPolicy(base=1.0, factor=2.0, cap=4.0)
    for attempt in range(10):
        delay = policy.backoff(attempt)
        assert 0 <= delay <= policy.cap


def test_retry_policy_backoff_grows():
    policy = RetryPolicy(base=1.0, factor=2.0, cap=100.0)
    # Pin random.uniform to return its upper bound so we get deterministic ceiling values.
    with patch("arcana.models.gateway.random.uniform", side_effect=lambda _, hi: hi):
        ceiling_0 = policy.backoff(0)  # min(100, 1.0 * 2^0) = 1.0
        ceiling_2 = policy.backoff(2)  # min(100, 1.0 * 2^2) = 4.0
    assert ceiling_0 < ceiling_2


# ---------------------------------------------------------------------------
# PricingTable
# ---------------------------------------------------------------------------


def test_pricing_table_known_model():
    table = PricingTable({"x/y": (1.0, 2.0)})
    cost = table.cost("x/y", 1000, 500)
    assert cost == pytest.approx((1.0 * 1000 + 2.0 * 500) / 1000)


def test_pricing_table_unknown_no_connection_returns_none():
    table = PricingTable()
    assert table.cost("ollama/hermes-3", 999, 999) is None


def test_pricing_table_override():
    table = PricingTable()
    table.override("foo/bar", 0.5, 1.0)
    assert table.cost("foo/bar", 2000, 0) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_pricing_unknown_cloud_model_flagged_not_zero():
    """Unknown cloud models must produce priced=False, not silently bill at $0."""
    adapter = _make_adapter(response=_ok_response(input_tokens=100, output_tokens=50))
    registry = _make_registry(adapter)
    store = _make_store()
    events: list[CostEvent] = []
    gw = ModelGateway(
        connections=store,
        providers=registry,
        pricing=PricingTable({}),  # empty — no entry for this model
        on_cost=events.append,
    )

    await gw.complete("anthropic/claude-new-unknown", _req())
    assert len(events) == 1
    ev = events[0]
    assert ev.priced is False
    assert ev.usage.cost is None


@pytest.mark.asyncio
async def test_pricing_local_model_zero_with_tokens():
    """Local (Ollama) models cost $0 but tokens are still counted."""
    adapter = _make_adapter(response=_ok_response(input_tokens=100, output_tokens=50))
    registry = _make_registry(adapter)
    store = _make_store()
    events: list[CostEvent] = []
    gw = ModelGateway(
        connections=store,
        providers=registry,
        pricing=PricingTable({}),  # empty — no entry, must fall back to is_local
        on_cost=events.append,
    )

    await gw.complete("ollama/hermes-3", _req())
    assert len(events) == 1
    ev = events[0]
    assert ev.priced is True
    assert ev.usage.cost == 0.0
    assert ev.usage.prompt_tokens == 100
    assert ev.usage.completion_tokens == 50


@pytest.mark.asyncio
async def test_pricing_connection_override_wins():
    """Per-connection cost_per_1k_* fields take priority over the global pricing table."""
    adapter = _make_adapter(response=_ok_response(input_tokens=1000, output_tokens=500))
    conn = ModelConnection(
        name="my-custom-claude",
        provider=ModelProvider.ANTHROPIC,
        model_id="claude-sonnet-4-6",
        cost_per_1k_input_tokens=0.001,
        cost_per_1k_output_tokens=0.002,
    )
    store = MagicMock(spec=ConnectionStore)
    store.get_by_provider.return_value = conn
    store.get_api_key.return_value = None

    events: list[CostEvent] = []
    gw = ModelGateway(
        connections=store,
        providers=_make_registry(adapter),
        pricing=PricingTable({"anthropic/claude-sonnet-4-6": (99.0, 99.0)}),  # should be ignored
        on_cost=events.append,
    )

    await gw.complete("anthropic/claude-sonnet-4-6", _req())
    assert len(events) == 1
    ev = events[0]
    assert ev.priced is True
    expected_cost = (0.001 * 1000 + 0.002 * 500) / 1000
    assert ev.usage.cost == pytest.approx(expected_cost)


# ---------------------------------------------------------------------------
# Usage / CostEvent
# ---------------------------------------------------------------------------


def test_usage_total():
    u = Usage.from_tokens(10, 20)
    assert u.total == 30
    assert u.cost is None


def test_usage_with_cost():
    u = Usage.from_tokens(100, 50, cost=0.42)
    assert u.cost == pytest.approx(0.42)


def test_cost_event_has_timestamp():
    u = Usage.from_tokens(0, 0)
    ev = CostEvent(model="x/y", usage=u)
    assert ev.timestamp is not None


# ---------------------------------------------------------------------------
# ConnectionStore
# ---------------------------------------------------------------------------


def test_connection_store_empty_when_file_missing(tmp_path):
    store = ConnectionStore(path=tmp_path / "nonexistent.json")
    assert store.all() == []


def test_connection_store_loads_connections(tmp_path):
    conn_path = tmp_path / "models.json"
    conn = ModelConnection(
        name="my-ollama",
        provider=ModelProvider.OLLAMA,
        model_id="llama3",
    )
    conn_path.write_text(f"[{conn.model_dump_json()}]")

    store = ConnectionStore(path=conn_path)
    assert len(store.all()) == 1
    assert store.all()[0].name == "my-ollama"


def test_connection_store_get_by_provider(tmp_path):
    conn_path = tmp_path / "models.json"
    conn = ModelConnection(
        name="claude",
        provider=ModelProvider.ANTHROPIC,
        model_id="claude-sonnet-4-6",
    )
    conn_path.write_text(f"[{conn.model_dump_json()}]")

    store = ConnectionStore(path=conn_path)
    found = store.get_by_provider(ModelProvider.ANTHROPIC)
    assert found is not None
    assert found.model_id == "claude-sonnet-4-6"


def test_connection_store_get_by_provider_missing(tmp_path):
    store = ConnectionStore(path=tmp_path / "empty.json")
    assert store.get_by_provider(ModelProvider.ANTHROPIC) is None


def test_connection_store_get_by_name(tmp_path):
    conn_path = tmp_path / "models.json"
    conn = ModelConnection(name="my-model", provider=ModelProvider.OLLAMA, model_id="llama3")
    conn_path.write_text(f"[{conn.model_dump_json()}]")

    store = ConnectionStore(path=conn_path)
    assert store.get_by_name("my-model") is not None
    assert store.get_by_name("other") is None


def test_connection_store_reload(tmp_path):
    conn_path = tmp_path / "models.json"
    conn_path.write_text("[]")

    store = ConnectionStore(path=conn_path)
    assert store.all() == []

    conn = ModelConnection(name="x", provider=ModelProvider.OLLAMA, model_id="llama3")
    conn_path.write_text(f"[{conn.model_dump_json()}]")

    store.reload()
    assert len(store.all()) == 1


# ---------------------------------------------------------------------------
# ModelGateway.resolve()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_valid_string():
    adapter = _make_adapter()
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    conn = gw.resolve("ollama/hermes-3")
    assert conn.model_id == "hermes-3"


def test_resolve_invalid_string_raises():
    store = _make_store()
    gw = ModelGateway(connections=store)

    with pytest.raises(ValueError, match="Invalid model string"):
        gw.resolve("no-slash-here")

    with pytest.raises(ValueError, match="Invalid model string"):
        gw.resolve("/missing-provider")

    with pytest.raises(ValueError, match="Invalid model string"):
        gw.resolve("missing-model/")


def test_resolve_uses_stored_connection(tmp_path):
    conn_path = tmp_path / "models.json"
    conn = ModelConnection(
        name="my-anthropic",
        provider=ModelProvider.ANTHROPIC,
        model_id="claude-opus-4-8",
        endpoint="",
    )
    conn_path.write_text(f"[{conn.model_dump_json()}]")

    store = ConnectionStore(path=conn_path)
    gw = ModelGateway(connections=store)

    resolved = gw.resolve("anthropic/claude-sonnet-4-6")
    # model_id overridden from the string, not the stored connection
    assert resolved.model_id == "claude-sonnet-4-6"
    assert resolved.provider == ModelProvider.ANTHROPIC


def test_resolve_falls_back_to_defaults_when_no_stored_connection():
    store = _make_store()
    gw = ModelGateway(connections=store)

    conn = gw.resolve("ollama/llama3")
    assert conn.provider == ModelProvider.OLLAMA
    assert conn.endpoint == "http://localhost:11434"
    assert conn.model_id == "llama3"


def test_resolve_lmstudio_alias():
    store = _make_store()
    gw = ModelGateway(connections=store)
    conn = gw.resolve("lmstudio/phi-3")
    assert conn.provider == ModelProvider.OPENAI_COMPAT


# ---------------------------------------------------------------------------
# ModelGateway.complete() — dispatch and cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_dispatches_to_adapter():
    adapter = _make_adapter(response=_ok_response("hello"))
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    result = await gw.complete("ollama/hermes-3", _req())
    assert result.content == "hello"
    adapter.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_injects_model_id_into_request():
    adapter = _make_adapter()
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    await gw.complete("ollama/hermes-3", _req())
    call_args = adapter.complete.call_args
    req_sent: CompletionRequest = call_args.args[0]
    assert req_sent.model_id == "hermes-3"


@pytest.mark.asyncio
async def test_complete_emits_cost_event():
    adapter = _make_adapter(response=_ok_response(input_tokens=100, output_tokens=50))
    registry = _make_registry(adapter)
    store = _make_store()
    events: list[CostEvent] = []
    gw = ModelGateway(
        connections=store,
        providers=registry,
        pricing=PricingTable({"ollama/hermes-3": (1.0, 2.0)}),
        on_cost=events.append,
    )

    await gw.complete("ollama/hermes-3", _req())
    assert len(events) == 1
    ev = events[0]
    assert ev.model == "ollama/hermes-3"
    assert ev.usage.prompt_tokens == 100
    assert ev.usage.completion_tokens == 50
    assert ev.usage.cost == pytest.approx((1.0 * 100 + 2.0 * 50) / 1000)


# ---------------------------------------------------------------------------
# ModelGateway.complete() — retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_retries_transient_error():
    call_count = 0

    async def flaky(*_):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ModelTransientError("temporary")
        return _ok_response()

    adapter = _make_adapter(side_effect=flaky)
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(
        connections=store,
        providers=registry,
        retry=RetryPolicy(max_retries=3, base=0.0),
    )

    result = await gw.complete("ollama/hermes-3", _req())
    assert result.content == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_complete_raises_after_max_retries():
    adapter = _make_adapter(side_effect=ModelUnavailableError("down"))
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(
        connections=store,
        providers=registry,
        retry=RetryPolicy(max_retries=2, base=0.0),
    )

    with pytest.raises(ModelUnavailableError):
        await gw.complete("ollama/hermes-3", _req())

    assert adapter.complete.await_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_complete_does_not_retry_fatal_errors():
    for fatal in [ModelAuthError("bad key"), ModelBadRequestError("bad req")]:
        adapter = _make_adapter(side_effect=fatal)
        registry = _make_registry(adapter)
        store = _make_store()
        gw = ModelGateway(connections=store, providers=registry, retry=RetryPolicy(max_retries=3, base=0.0))

        with pytest.raises(type(fatal)):
            await gw.complete("ollama/hermes-3", _req())

        assert adapter.complete.await_count == 1


@pytest.mark.asyncio
async def test_complete_honours_retry_after():
    delays_slept: list[float] = []

    async def mock_sleep(secs: float) -> None:
        delays_slept.append(secs)

    call_count = 0

    async def flaky(*_):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ModelTransientError("429", retry_after=2.5)
        return _ok_response()

    adapter = _make_adapter(side_effect=flaky)
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry, retry=RetryPolicy(max_retries=2, base=0.0))

    with patch("arcana.models.gateway.asyncio.sleep", side_effect=mock_sleep):
        await gw.complete("ollama/hermes-3", _req())

    assert delays_slept == [2.5]


# ---------------------------------------------------------------------------
# ModelGateway.stream()
# ---------------------------------------------------------------------------


async def _aiter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_stream_yields_model_chunks():
    adapter = _make_adapter()
    adapter.stream = MagicMock(return_value=_aiter([ModelChunk(text="hello"), ModelChunk(text=" world")]))
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    chunks = [c async for c in gw.stream("ollama/hermes-3", _req())]
    assert [c.text for c in chunks] == ["hello", " world"]


@pytest.mark.asyncio
async def test_stream_retries_before_first_token():
    call_count = 0

    async def flaky_stream(*_):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ModelUnavailableError("not yet")
        yield ModelChunk(text="hello")

    adapter = _make_adapter()
    adapter.stream = MagicMock(side_effect=flaky_stream)
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(
        connections=store,
        providers=registry,
        retry=RetryPolicy(max_retries=3, base=0.0),
    )

    with patch("arcana.models.gateway.asyncio.sleep", new_callable=AsyncMock):
        chunks = [c async for c in gw.stream("ollama/hermes-3", _req())]

    assert [c.text for c in chunks] == ["hello"]
    assert call_count == 2


@pytest.mark.asyncio
async def test_stream_emits_cost_event():
    adapter = _make_adapter()
    adapter.stream = MagicMock(
        return_value=_aiter(
            [
                ModelChunk(text="hello"),
                ModelChunk(text=" world"),
                ModelChunk(text="", input_tokens=10, output_tokens=5),
            ]
        )
    )
    registry = _make_registry(adapter)
    store = _make_store()
    events: list[CostEvent] = []
    gw = ModelGateway(
        connections=store,
        providers=registry,
        pricing=PricingTable({"ollama/hermes-3": (1.0, 2.0)}),
        on_cost=events.append,
    )

    _ = [c async for c in gw.stream("ollama/hermes-3", _req())]

    assert len(events) == 1
    ev = events[0]
    assert ev.model == "ollama/hermes-3"
    assert ev.usage.prompt_tokens == 10
    assert ev.usage.completion_tokens == 5
    assert ev.usage.cost == pytest.approx((1.0 * 10 + 2.0 * 5) / 1000)
    assert ev.estimated is False


@pytest.mark.asyncio
async def test_stream_propagates_usage_on_final_chunk():
    adapter = _make_adapter()
    adapter.stream = MagicMock(
        return_value=_aiter(
            [
                ModelChunk(text="hello"),
                ModelChunk(text="", input_tokens=10, output_tokens=5),
            ]
        )
    )
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    chunks = [c async for c in gw.stream("ollama/hermes-3", _req())]

    final = chunks[-1]
    assert final.input_tokens == 10
    assert final.output_tokens == 5


@pytest.mark.asyncio
async def test_stream_estimates_cost_when_usage_missing():
    text = "hello world"  # 11 chars → 11 // 4 = 2 output tokens
    adapter = _make_adapter()
    adapter.stream = MagicMock(return_value=_aiter([ModelChunk(text=text)]))
    registry = _make_registry(adapter)
    store = _make_store()
    events: list[CostEvent] = []
    gw = ModelGateway(
        connections=store,
        providers=registry,
        pricing=PricingTable({"ollama/hermes-3": (1.0, 2.0)}),
        on_cost=events.append,
    )

    _ = [c async for c in gw.stream("ollama/hermes-3", _req())]

    assert len(events) == 1
    assert events[0].estimated is True
    assert events[0].usage.completion_tokens == len(text) // 4


@pytest.mark.asyncio
async def test_stream_closes_response_on_early_break():
    closed = False

    async def mock_stream_gen(*_):
        nonlocal closed
        try:
            yield ModelChunk(text="first")
            yield ModelChunk(text="second")
        finally:
            closed = True

    adapter = _make_adapter()
    adapter.stream = MagicMock(side_effect=mock_stream_gen)
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    async with aclosing(gw.stream("ollama/hermes-3", _req())) as s:
        async for _ in s:
            break

    assert closed


# ---------------------------------------------------------------------------
# ModelGateway.health()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_single_model():
    adapter = _make_adapter()
    adapter.health_check = AsyncMock(return_value=ModelHealth(healthy=True, model_id="hermes-3"))
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    result = await gw.health("ollama/hermes-3")
    assert "ollama/hermes-3" in result
    assert result["ollama/hermes-3"].healthy is True


@pytest.mark.asyncio
async def test_health_all_cached():
    adapter = _make_adapter()
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    await gw.complete("ollama/hermes-3", _req())  # warms cache
    all_health = await gw.health()
    assert len(all_health) >= 1


# ---------------------------------------------------------------------------
# Adapter caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_cached_per_connection():
    adapter = _make_adapter()
    factory = MagicMock(return_value=adapter)
    entry = ProviderEntry(factory=factory, default_endpoint="http://localhost:11434")
    registry = ProviderRegistry({"ollama": entry})
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    await gw.complete("ollama/hermes-3", _req())
    await gw.complete("ollama/llama3", _req())

    # Same Ollama server → factory called once, adapter reused
    assert factory.call_count == 1
    assert adapter.connect.await_count == 1


@pytest.mark.asyncio
async def test_aclose_calls_adapter_aclose():
    adapter = _make_adapter()
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(connections=store, providers=registry)

    await gw.complete("ollama/hermes-3", _req())
    await gw.aclose()

    adapter.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_manager_closes_on_exit():
    adapter = _make_adapter()
    registry = _make_registry(adapter)
    store = _make_store()

    async with ModelGateway(connections=store, providers=registry) as gw:
        await gw.complete("ollama/hermes-3", _req())

    adapter.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------


def test_cache_key_same_connection():
    k1 = _cache_key("ollama", "http://localhost:11434", None)
    k2 = _cache_key("ollama", "http://localhost:11434", None)
    assert k1 == k2


def test_cache_key_different_api_keys():
    k1 = _cache_key("anthropic", "", "key-a")
    k2 = _cache_key("anthropic", "", "key-b")
    assert k1 != k2


def test_cache_key_different_endpoints():
    k1 = _cache_key("openai_compat", "http://localhost:1234", None)
    k2 = _cache_key("openai_compat", "http://localhost:5678", None)
    assert k1 != k2


# ---------------------------------------------------------------------------
# Cost sink error isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_response_when_cost_sink_raises():
    adapter = _make_adapter(response=_ok_response())
    registry = _make_registry(adapter)
    store = _make_store()

    def bad_sink(event: CostEvent) -> None:
        raise RuntimeError("sink exploded")

    gw = ModelGateway(connections=store, providers=registry, on_cost=bad_sink)

    result = await gw.complete("ollama/hermes-3", _req())
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_cost_sink_failure_is_logged(caplog):
    import logging

    adapter = _make_adapter(response=_ok_response())
    registry = _make_registry(adapter)
    store = _make_store()

    def bad_sink(event: CostEvent) -> None:
        raise RuntimeError("sink exploded")

    gw = ModelGateway(connections=store, providers=registry, on_cost=bad_sink)

    with caplog.at_level(logging.WARNING, logger="arcana.models.gateway"):
        await gw.complete("ollama/hermes-3", _req())

    assert any("on_cost" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_stream_returns_chunks_when_cost_sink_raises():
    adapter = _make_adapter()
    adapter.stream = MagicMock(return_value=_aiter([ModelChunk(text="hello"), ModelChunk(text=" world")]))
    registry = _make_registry(adapter)
    store = _make_store()

    def bad_sink(event: CostEvent) -> None:
        raise RuntimeError("sink exploded")

    gw = ModelGateway(connections=store, providers=registry, on_cost=bad_sink)

    chunks = [c async for c in gw.stream("ollama/hermes-3", _req())]
    assert [c.text for c in chunks] == ["hello", " world"]


# ---------------------------------------------------------------------------
# Health-based fast-fail (circuit-breaker-lite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unhealthy_connection_fails_fast_within_cooldown():
    adapter = _make_adapter(side_effect=ModelUnavailableError("down"))
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(
        connections=store,
        providers=registry,
        retry=RetryPolicy(max_retries=0, base=0.0),
        unhealthy_cooldown=60.0,
    )

    with pytest.raises(ModelUnavailableError):
        await gw.complete("ollama/hermes-3", _req())

    calls_after_first = adapter.complete.await_count

    # Second call within cooldown must fast-fail — no additional adapter calls.
    with pytest.raises(ModelUnavailableError):
        await gw.complete("ollama/hermes-3", _req())

    assert adapter.complete.await_count == calls_after_first


@pytest.mark.asyncio
async def test_connection_recovers_after_cooldown_probe():
    call_count = 0

    async def down_then_up(*_):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ModelUnavailableError("down")
        return _ok_response()

    adapter = _make_adapter(side_effect=down_then_up)
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(
        connections=store,
        providers=registry,
        retry=RetryPolicy(max_retries=0, base=0.0),
        unhealthy_cooldown=1.0,
    )

    with pytest.raises(ModelUnavailableError):
        await gw.complete("ollama/hermes-3", _req())

    # Backdate unhealthy_since to simulate cooldown elapsed.
    cache_entry = list(gw._cache.values())[0]
    cache_entry.unhealthy_since -= 2.0

    result = await gw.complete("ollama/hermes-3", _req())
    assert result.content == "ok"
    assert cache_entry.healthy is True


@pytest.mark.asyncio
async def test_health_check_resets_unhealthy_flag():
    adapter = _make_adapter(side_effect=ModelUnavailableError("down"))
    adapter.health_check = AsyncMock(return_value=ModelHealth(healthy=True, model_id="hermes-3"))
    registry = _make_registry(adapter)
    store = _make_store()
    gw = ModelGateway(
        connections=store,
        providers=registry,
        retry=RetryPolicy(max_retries=0, base=0.0),
        unhealthy_cooldown=60.0,
    )

    with pytest.raises(ModelUnavailableError):
        await gw.complete("ollama/hermes-3", _req())

    await gw.health("ollama/hermes-3")

    cache_entry = list(gw._cache.values())[0]
    assert cache_entry.healthy is True

    # After health check reset, complete must reach the adapter again.
    adapter.complete = AsyncMock(return_value=_ok_response())
    result = await gw.complete("ollama/hermes-3", _req())
    assert result.content == "ok"
