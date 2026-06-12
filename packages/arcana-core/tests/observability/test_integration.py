"""Integration tests: run an Agent and verify audit log + metrics output."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcana.agents.agent import Agent
from arcana.models.adapters.base import CompletionResponse, ModelChunk, ModelHealth
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway, ProviderEntry, ProviderRegistry
from arcana.observability import configure_observability, get_audit_log
from arcana.types.card import Card
from arcana.types.model import ModelProvider


@pytest.fixture(autouse=True)
def isolated_observability(tmp_path, monkeypatch):
    """Configure observability with a temp directory for each test."""
    configure_observability(base_dir=tmp_path / "arcana")
    # Reset metrics singleton so each test gets a fresh instance
    import arcana.observability.metrics as _m

    monkeypatch.setattr(_m, "_metrics", None)
    yield


@pytest.fixture
def mock_gateway():
    gw = MagicMock(spec=ModelGateway)
    gw.complete = AsyncMock(
        return_value=CompletionResponse(content="Test response.", input_tokens=20, output_tokens=10)
    )

    async def _stream(_model: str, _req: object) -> AsyncGenerator[ModelChunk, None]:
        yield ModelChunk(text="Test ", input_tokens=0, output_tokens=0)
        yield ModelChunk(text="response.", input_tokens=20, output_tokens=10)

    gw.stream = _stream
    return gw


@pytest.fixture
def agent(mock_gateway):
    return Agent(name="tester", card=Card.HERMIT, gateway=mock_gateway, model="ollama/test")


@pytest.mark.asyncio
async def test_run_emits_session_event(agent):
    await agent.run("hello world")

    log = get_audit_log()
    assert log is not None
    session_events = log.tail(event_type="session")
    assert len(session_events) == 1

    ev = session_events[0]
    assert ev["type"] == "session"
    assert ev["agent_name"] == "tester"
    assert ev["card"] == Card.HERMIT.value
    assert ev["model"] == "ollama/test"
    assert ev["input_tokens"] == 20
    assert ev["output_tokens"] == 10
    assert ev["status"] == "completed"


@pytest.mark.asyncio
async def test_run_emits_model_call_event():
    """Uses a real ModelGateway (with mocked adapter) so _emit_model_call fires."""
    mock_adapter = MagicMock()
    mock_adapter.connect = AsyncMock()
    mock_adapter.aclose = AsyncMock()
    mock_adapter.health_check = AsyncMock(return_value=ModelHealth(healthy=True, model_id="test"))
    mock_adapter.complete = AsyncMock(return_value=CompletionResponse(content="hi", input_tokens=20, output_tokens=10))

    store = MagicMock(spec=ConnectionStore)
    store.get_by_provider.return_value = None
    store.get_by_name.return_value = None
    store.get_api_key.return_value = None

    factory = MagicMock(return_value=mock_adapter)
    registry = ProviderRegistry(
        {
            "ollama": ProviderEntry(
                factory=factory, default_endpoint="http://localhost:11434", provider=ModelProvider.OLLAMA
            )
        }
    )

    async with ModelGateway(connections=store, providers=registry) as gw:
        agent = Agent(name="tester", card=Card.HERMIT, gateway=gw, model="ollama/test")
        await agent.run("hello world")

    log = get_audit_log()
    assert log is not None
    model_events = log.tail(event_type="model_call")
    assert len(model_events) == 1

    ev = model_events[0]
    assert ev["type"] == "model_call"
    assert ev["model"] == "ollama/test"
    assert ev["success"] is True
    assert ev["input_tokens"] == 20
    assert ev["output_tokens"] == 10
    assert ev["attempt"] == 1


@pytest.mark.asyncio
async def test_stream_emits_session_event(agent):
    chunks = []
    async for chunk in agent.stream("hello world"):
        chunks.append(chunk)

    assert "".join(chunks) != ""

    log = get_audit_log()
    assert log is not None
    session_events = log.tail(event_type="session")
    assert len(session_events) == 1
    assert session_events[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_multiple_runs_accumulate_events(agent):
    await agent.run("first")
    await agent.run("second")

    log = get_audit_log()
    assert log is not None
    session_events = log.tail(event_type="session")
    assert len(session_events) == 2


@pytest.mark.asyncio
async def test_session_event_has_modifier_cards(mock_gateway):
    agent = Agent(
        name="blended",
        card=Card.HERMIT,
        modifier_cards=[Card.FOOL],
        gateway=mock_gateway,
        model="ollama/test",
    )
    await agent.run("hello")

    log = get_audit_log()
    assert log is not None
    ev = log.tail(event_type="session")[0]
    assert Card.FOOL.value in ev["modifier_cards"]


@pytest.mark.asyncio
async def test_observability_not_configured_does_not_crash(mock_gateway):
    """If configure_observability() was never called, agent.run() must not crash."""
    import arcana.observability as _obs
    import arcana.observability.metrics as _m

    original = _obs._audit_log
    original_metrics = _m._metrics
    try:
        _obs._audit_log = None
        _m._metrics = None
        agent = Agent(name="x", card=Card.FOOL, gateway=mock_gateway, model="ollama/test")
        result = await agent.run("hello")
        assert result == "Test response."
    finally:
        _obs._audit_log = original
        _m._metrics = original_metrics
