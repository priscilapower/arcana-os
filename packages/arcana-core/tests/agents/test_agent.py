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


# ---------------------------------------------------------------------------
# _build_system — soul injection
# ---------------------------------------------------------------------------


def test_build_system_includes_user_context_block_when_soul_set(gateway):
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", soul="## Preferences\n- concise")
    system = ag._build_system("", None)
    assert "─── USER CONTEXT ───" in system
    assert "## Preferences\n- concise" in system
    assert "─── END USER CONTEXT ───" in system


def test_build_system_omits_user_context_block_when_soul_none(gateway):
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", soul=None)
    system = ag._build_system("", None)
    assert "USER CONTEXT" not in system


def test_build_system_ordering_card_soul_memory_extra(gateway):
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", soul="soul content")
    system = ag._build_system("memory content", "extra content")
    soul_pos = system.index("soul content")
    memory_pos = system.index("memory content")
    extra_pos = system.index("extra content")
    assert soul_pos < memory_pos < extra_pos


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


@pytest.mark.asyncio
async def test_agent_run_cost_event_has_session_id():
    adapter = _stub_adapter()
    cost_events: list[CostEvent] = []
    async with _real_gateway(adapter, on_cost=cost_events.append) as gw:
        ag = Agent(name="test", card=Card.FOOL, gateway=gw, model="ollama/hermes-3")
        await ag.run("hello")

    assert len(cost_events) == 1
    ev = cost_events[0]
    assert ev.metadata is not None
    session = ag._sessions[0]
    assert ev.metadata["session_id"] == str(session.id)
    assert ev.metadata["agent_id"] == str(ag.id)


# ---------------------------------------------------------------------------
# Conversation continuity — history replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_session_replays_prior_messages(gateway):
    """Passing a pre-populated session sends full history to the model."""
    from arcana.types.session import Session

    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test")
    session = Session(agent_id=ag.id)
    session.add_message(MessageRole.USER, "first turn")
    session.add_message(MessageRole.ASSISTANT, "first response")

    await ag.run("second turn", session=session)

    call_args: CompletionRequest = gateway.complete.call_args[0][1]
    roles = [m["role"] for m in call_args.messages]
    assert roles == ["user", "assistant", "user"]
    assert call_args.messages[0]["content"] == "first turn"
    assert call_args.messages[2]["content"] == "second turn"


@pytest.mark.asyncio
async def test_run_session_persisted_and_resumed(gateway, tmp_path):
    """Running, then loading and resuming a session sends both turns to the model."""
    from arcana.agents.session_manager import SessionManager

    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", id=agent_id, session_manager=sm)

    await ag.run("first prompt")

    persisted = sm.list_sessions(agent_id)
    assert len(persisted) == 1
    loaded_session = sm.load(agent_id, persisted[0].id)
    assert loaded_session is not None

    # Reset call tracking so we only inspect the second call
    gateway.complete.reset_mock()
    await ag.run("second prompt", session=loaded_session)

    call_args: CompletionRequest = gateway.complete.call_args[0][1]
    roles = [m["role"] for m in call_args.messages]
    assert "user" in roles
    assert "assistant" in roles
    assert call_args.messages[-1]["content"] == "second prompt"

    # Both turns persisted under the same session id
    sessions_after = sm.list_sessions(agent_id)
    assert len(sessions_after) == 2 or (len(sessions_after) == 1 and len(sessions_after[0].messages) >= 4)


@pytest.mark.asyncio
async def test_history_cap_trims_request_but_not_disk(gateway, tmp_path):
    """Messages beyond MAX_HISTORY_TURNS * 2 are dropped from the request, not from disk."""
    from arcana.agents.agent import MAX_HISTORY_TURNS
    from arcana.agents.session_manager import SessionManager
    from arcana.types.session import Session

    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(name="x", card=Card.HERMIT, gateway=gateway, model="ollama/test", id=agent_id, session_manager=sm)

    # Build a session with MAX_HISTORY_TURNS full turns already in it
    session = Session(agent_id=agent_id)
    for i in range(MAX_HISTORY_TURNS):
        session.add_message(MessageRole.USER, f"old user {i}")
        session.add_message(MessageRole.ASSISTANT, f"old assistant {i}")

    # Now run — this adds one more user message (total 41 messages for the cap window)
    await ag.run("new prompt", session=session)

    call_args: CompletionRequest = gateway.complete.call_args[0][1]
    # Request is capped at MAX_HISTORY_TURNS * 2 messages
    assert len(call_args.messages) <= MAX_HISTORY_TURNS * 2

    # But all messages are on disk
    # (MAX_HISTORY_TURNS user + MAX_HISTORY_TURNS assistant + 1 new user + 1 new assistant)
    persisted = sm.list_sessions(agent_id)
    assert len(persisted) == 1
    total_msg_count = MAX_HISTORY_TURNS * 2 + 2  # old turns + new user + new assistant
    assert len(persisted[0].messages) == total_msg_count
