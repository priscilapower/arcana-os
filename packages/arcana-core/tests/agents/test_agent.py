"""Tests for the Agent runtime class."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from arcana.agents.agent import Agent
from arcana.agents.session_manager import SessionManager
from arcana.models.adapters.base import CompletionRequest, CompletionResponse, ModelHealth
from arcana.models.connection_store import ConnectionStore
from arcana.models.errors import ModelTransientError
from arcana.models.gateway import ModelGateway, ProviderEntry, ProviderRegistry, RetryPolicy
from arcana.models.pricing import CostEvent
from arcana.types.card import Card
from arcana.types.model import ModelProvider
from arcana.types.session import MessageRole, SessionStatus

# ---------------------------------------------------------------------------
# Helpers for real-gateway tests
# ---------------------------------------------------------------------------


def _stub_adapter(*, content: str = "Hello from the agent.", side_effect=None) -> MagicMock:
    adapter = MagicMock()
    adapter.connect = AsyncMock()
    adapter.aclose = AsyncMock()
    adapter.health_check = AsyncMock(return_value=ModelHealth(healthy=True, model_id="test"))
    if side_effect is not None:
        adapter.complete = AsyncMock(side_effect=side_effect)
    else:
        adapter.complete = AsyncMock(
            return_value=CompletionResponse(content=content, input_tokens=10, output_tokens=5)
        )
    return adapter


def _real_gateway(adapter: MagicMock, *, on_cost=None, retry: RetryPolicy | None = None) -> ModelGateway:
    factory = MagicMock(return_value=adapter)
    entry = ProviderEntry(factory=factory, default_endpoint="", provider=ModelProvider.OLLAMA)
    registry = ProviderRegistry({"ollama": entry})
    store = MagicMock(spec=ConnectionStore)
    store.get_by_provider.return_value = None
    store.get_by_name.return_value = None
    store.get_api_key.return_value = None
    return ModelGateway(store, providers=registry, on_cost=on_cost, retry=retry)


# ---------------------------------------------------------------------------
# run() — basic behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_response_content(agent):
    result = await agent.run("What is RAG?")
    assert result == "Hello from the agent."


@pytest.mark.asyncio
async def test_run_passes_system_prompt_to_gateway(agent, gateway):
    await agent.run("test prompt")
    call_args: CompletionRequest = gateway.complete.call_args[0][1]
    assert "Researcher" in call_args.system  # Hermit's role


@pytest.mark.asyncio
async def test_run_extra_context_appended_to_system(agent, gateway):
    await agent.run("question", context="extra facts here")
    call_args: CompletionRequest = gateway.complete.call_args[0][1]
    assert "extra facts here" in call_args.system


@pytest.mark.asyncio
async def test_run_uses_card_temperature(agent, gateway):
    await agent.run("question")
    call_args: CompletionRequest = gateway.complete.call_args[0][1]
    assert abs(call_args.temperature - 0.35) < 0.01  # Hermit default


@pytest.mark.asyncio
async def test_run_records_session_internally(agent):
    await agent.run("prompt one")
    await agent.run("prompt two")
    assert len(agent._sessions) == 2


@pytest.mark.asyncio
async def test_run_session_has_user_and_assistant_messages(agent):
    await agent.run("hello")
    session = agent._sessions[0]
    roles = [m.role for m in session.messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


@pytest.mark.asyncio
async def test_run_session_is_completed(agent):
    await agent.run("hello")
    assert agent._sessions[0].status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_tracks_token_counts(agent):
    await agent.run("hello")
    session = agent._sessions[0]
    assert session.total_input_tokens == 10
    assert session.total_output_tokens == 5


# ---------------------------------------------------------------------------
# stream() — basic behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_yields_chunks(agent):
    chunks = []
    async for chunk in agent.stream("stream this"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert "Hello" in "".join(chunks)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_agent_gets_uuid_by_default(gateway):
    ag = Agent(name="x", card=Card.FOOL, gateway=gateway, model="ollama/test")
    assert isinstance(ag.id, UUID)


def test_agent_accepts_explicit_id(gateway):
    fixed_id = uuid4()
    ag = Agent(name="x", card=Card.FOOL, gateway=gateway, model="ollama/test", id=fixed_id)
    assert ag.id == fixed_id


def test_agent_card_config_reflects_card(gateway):
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test")
    assert ag.card_config.temperature == 0.35
    assert ag.card_config.memory_weights.semantic == 0.95


def test_system_prompt_override_replaces_card_prompt(gateway):
    ag = Agent(
        name="x",
        card=Card.HERMIT,
        gateway=gateway,
        model="ollama/test",
        system_prompt_override="You are a pirate.",
    )
    assert ag._system_prompt == "You are a pirate."


def test_modifier_card_blends_temperature(gateway):
    # Hermit (0.35) primary + Empress (0.85) modifier → ~0.50
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", modifier_cards=[Card.EMPRESS])
    assert abs(ag.card_config.temperature - 0.50) < 0.01


# ---------------------------------------------------------------------------
# SessionManager integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_session_manager_persists_session(gateway, tmp_path):
    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", id=agent_id, session_manager=sm)

    await ag.run("persist me")

    sessions = sm.list_sessions(agent_id)
    assert len(sessions) == 1
    assert sessions[0].status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_with_session_manager_records_messages(gateway, tmp_path):
    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", id=agent_id, session_manager=sm)

    await ag.run("hello agent")

    session = sm.list_sessions(agent_id)[0]
    roles = [m.role for m in session.messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


# ---------------------------------------------------------------------------
# Gateway integration — new tests (MG-10 acceptance criteria)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_run_uses_gateway_and_emits_cost():
    adapter = _stub_adapter()
    cost_events: list[CostEvent] = []
    async with _real_gateway(adapter, on_cost=cost_events.append) as gw:
        ag = Agent(name="test", card=Card.FOOL, gateway=gw, model="ollama/hermes-3")
        result = await ag.run("hello")

    assert result == "Hello from the agent."
    assert len(cost_events) == 1
    assert isinstance(cost_events[0], CostEvent)
    assert cost_events[0].model == "ollama/hermes-3"


@pytest.mark.asyncio
async def test_agent_run_retries_transient_via_gateway():
    call_count = 0

    async def flaky(req: CompletionRequest) -> CompletionResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ModelTransientError("temporary failure")
        return CompletionResponse(content="recovered", input_tokens=5, output_tokens=3)

    adapter = _stub_adapter(side_effect=flaky)
    async with _real_gateway(adapter, retry=RetryPolicy(max_retries=1, base=0.0)) as gw:
        ag = Agent(name="test", card=Card.FOOL, gateway=gw, model="ollama/test")
        result = await ag.run("hello")

    assert result == "recovered"
    assert call_count == 2


@pytest.mark.asyncio
async def test_agent_stream_records_session_tokens(agent):
    chunks = []
    async for chunk in agent.stream("stream this"):
        chunks.append(chunk)

    assert len(agent._sessions) == 1
    session = agent._sessions[0]
    assert session.status == SessionStatus.COMPLETED
    assert session.total_input_tokens == 10
    assert session.total_output_tokens == 5


@pytest.mark.asyncio
async def test_agent_routes_model_string_to_connection(gateway):
    ag = Agent(name="test", card=Card.FOOL, gateway=gateway, model="ollama/hermes-3")
    await ag.run("hello")

    model_arg = gateway.complete.call_args[0][0]
    assert model_arg == "ollama/hermes-3"
