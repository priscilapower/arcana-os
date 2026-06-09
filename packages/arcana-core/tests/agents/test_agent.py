"""Tests for the Agent runtime class."""

from uuid import UUID, uuid4

import pytest

from arcana.agents.agent import Agent
from arcana.agents.session_manager import SessionManager
from arcana.models.adapters.base import CompletionRequest
from arcana.types.card import Card
from arcana.types.session import MessageRole, SessionStatus


@pytest.mark.asyncio
async def test_run_returns_response_content(agent):
    result = await agent.run("What is RAG?")
    assert result == "Hello from the agent."


@pytest.mark.asyncio
async def test_run_passes_system_prompt_to_adapter(agent, adapter):
    await agent.run("test prompt")
    call_args: CompletionRequest = adapter.complete.call_args[0][0]
    assert "Researcher" in call_args.system  # Hermit's role


@pytest.mark.asyncio
async def test_run_extra_context_appended_to_system(agent, adapter):
    await agent.run("question", context="extra facts here")
    call_args: CompletionRequest = adapter.complete.call_args[0][0]
    assert "extra facts here" in call_args.system


@pytest.mark.asyncio
async def test_run_uses_card_temperature(agent, adapter):
    await agent.run("question")
    call_args: CompletionRequest = adapter.complete.call_args[0][0]
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


@pytest.mark.asyncio
async def test_stream_yields_chunks(agent):
    chunks = []
    async for chunk in agent.stream("stream this"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert "Hello" in "".join(chunks)


def test_agent_gets_uuid_by_default(adapter):
    ag = Agent(name="x", card=Card.FOOL, model=adapter)
    assert isinstance(ag.id, UUID)


def test_agent_accepts_explicit_id(adapter):
    fixed_id = uuid4()
    ag = Agent(name="x", card=Card.FOOL, model=adapter, id=fixed_id)
    assert ag.id == fixed_id


def test_agent_card_config_reflects_card(adapter):
    ag = Agent(name="x", card=Card.HERMIT, model=adapter)
    assert ag.card_config.temperature == 0.35
    assert ag.card_config.memory_weights.semantic == 0.95


def test_system_prompt_override_replaces_card_prompt(adapter):
    ag = Agent(
        name="x",
        card=Card.HERMIT,
        model=adapter,
        system_prompt_override="You are a pirate.",
    )
    assert ag._system_prompt == "You are a pirate."


@pytest.mark.asyncio
async def test_run_with_session_manager_persists_session(adapter, tmp_path):
    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(name="x", card=Card.HERMIT, model=adapter, id=agent_id, session_manager=sm)

    await ag.run("persist me")

    sessions = sm.list_sessions(agent_id)
    assert len(sessions) == 1
    assert sessions[0].status == SessionStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_with_session_manager_records_messages(adapter, tmp_path):
    sm = SessionManager(base_dir=tmp_path / "agents")
    agent_id = uuid4()
    ag = Agent(name="x", card=Card.HERMIT, model=adapter, id=agent_id, session_manager=sm)

    await ag.run("hello agent")

    session = sm.list_sessions(agent_id)[0]
    roles = [m.role for m in session.messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


def test_modifier_card_blends_temperature(adapter):
    # Hermit (0.35) primary + Empress (0.85) modifier → ~0.50
    ag = Agent(name="x", card=Card.HERMIT, model=adapter, modifier_cards=[Card.EMPRESS])
    assert abs(ag.card_config.temperature - 0.50) < 0.01
