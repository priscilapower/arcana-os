"""Tests for AgentRegistry."""

from uuid import uuid4

import pytest

from arcana.agents.registry import AgentRegistry
from arcana.types.card import Card
from arcana.types.session import MessageRole, SessionStatus

_TEST_MODEL = "ollama/test-model"


def test_create_returns_agent_record(tmp_registry: AgentRegistry):
    record = tmp_registry.create(name="researcher", card=Card.HERMIT, model=_TEST_MODEL)
    assert record.name == "researcher"
    assert record.card == Card.HERMIT
    assert record.model == _TEST_MODEL


def test_create_resolves_system_prompt_from_card(tmp_registry: AgentRegistry):
    record = tmp_registry.create(name="researcher", card=Card.HERMIT, model=_TEST_MODEL)
    assert "Researcher" in record.system_prompt


def test_create_resolves_temperature_from_card(tmp_registry: AgentRegistry):
    record = tmp_registry.create(name="researcher", card=Card.HERMIT, model=_TEST_MODEL)
    assert abs(record.temperature - 0.35) < 0.01


def test_create_accepts_system_prompt_override(tmp_registry: AgentRegistry):
    record = tmp_registry.create(
        name="custom",
        card=Card.HERMIT,
        model=_TEST_MODEL,
        system_prompt_override="You are a pirate.",
    )
    assert record.system_prompt == "You are a pirate."


def test_create_persists_to_disk(tmp_registry: AgentRegistry):
    record = tmp_registry.create(name="saved", card=Card.FOOL, model=_TEST_MODEL)
    agent_json = tmp_registry._base / str(record.id) / "agent.json"
    assert agent_json.exists()


def test_get_returns_record_by_id(tmp_registry: AgentRegistry):
    created = tmp_registry.create(name="findme", card=Card.MAGICIAN, model=_TEST_MODEL)
    found = tmp_registry.get(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.name == "findme"


def test_get_returns_none_for_unknown_id(tmp_registry: AgentRegistry):
    assert tmp_registry.get(uuid4()) is None


def test_list_returns_all_agents(tmp_registry: AgentRegistry):
    for name in ("alpha", "beta", "gamma"):
        tmp_registry.create(name=name, card=Card.FOOL, model=_TEST_MODEL)
    records = tmp_registry.list()
    assert len(records) == 3
    assert {r.name for r in records} == {"alpha", "beta", "gamma"}


def test_list_returns_empty_when_no_agents(tmp_registry: AgentRegistry):
    assert tmp_registry.list() == []


def test_list_sorts_by_name(tmp_registry: AgentRegistry):
    for name in ("zebra", "apple", "mango"):
        tmp_registry.create(name=name, card=Card.FOOL, model=_TEST_MODEL)
    names = [r.name for r in tmp_registry.list()]
    assert names == sorted(names)


def test_save_updates_existing_record(tmp_registry: AgentRegistry):
    record = tmp_registry.create(name="original", card=Card.FOOL, model=_TEST_MODEL)
    updated = record.model_copy(update={"name": "updated"})
    tmp_registry.save(updated)

    found = tmp_registry.get(record.id)
    assert found is not None
    assert found.name == "updated"


def test_delete_soft_archives_agent(tmp_registry: AgentRegistry):
    record = tmp_registry.create(name="doomed", card=Card.TOWER, model=_TEST_MODEL)
    tmp_registry.delete(record.id)

    found = tmp_registry.get(record.id)
    assert found is not None
    assert found.is_archived is True


def test_list_excludes_archived_agents(tmp_registry: AgentRegistry):
    kept = tmp_registry.create(name="kept", card=Card.FOOL, model=_TEST_MODEL)
    gone = tmp_registry.create(name="gone", card=Card.FOOL, model=_TEST_MODEL)
    tmp_registry.delete(gone.id)

    records = tmp_registry.list()
    assert len(records) == 1
    assert records[0].id == kept.id


def test_delete_raises_for_unknown_id(tmp_registry: AgentRegistry):
    with pytest.raises(FileNotFoundError):
        tmp_registry.delete(uuid4())


def test_create_with_modifier_cards(tmp_registry: AgentRegistry):
    record = tmp_registry.create(
        name="blended",
        card=Card.HERMIT,
        model=_TEST_MODEL,
        modifier_cards=[Card.MAGICIAN],
    )
    assert Card.MAGICIAN in record.modifier_cards


def test_create_with_tags(tmp_registry: AgentRegistry):
    record = tmp_registry.create(
        name="tagged",
        card=Card.FOOL,
        model=_TEST_MODEL,
        tags=["research", "phase-1"],
    )
    assert "research" in record.tags


def test_build_runtime_uses_record_id(tmp_registry: AgentRegistry, gateway):
    record = tmp_registry.create(name="runtime-test", card=Card.HERMIT, model=_TEST_MODEL)
    agent = tmp_registry.build_runtime(record, gateway)
    assert agent.id == record.id


def test_build_runtime_uses_record_system_prompt(tmp_registry: AgentRegistry, gateway):
    record = tmp_registry.create(
        name="rt",
        card=Card.HERMIT,
        model=_TEST_MODEL,
        system_prompt_override="Custom prompt.",
    )
    agent = tmp_registry.build_runtime(record, gateway)
    assert agent._system_prompt == "Custom prompt."


def test_build_runtime_uses_record_model(tmp_registry: AgentRegistry, gateway):
    record = tmp_registry.create(name="model-test", card=Card.HERMIT, model="anthropic/claude-sonnet-4-6")
    agent = tmp_registry.build_runtime(record, gateway)
    assert agent._model == "anthropic/claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# build_runtime() → run() round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_runtime_then_run_returns_response(tmp_registry: AgentRegistry, gateway):
    record = tmp_registry.create(name="pipeline-test", card=Card.FOOL, model=_TEST_MODEL)
    agent = tmp_registry.build_runtime(record, gateway)
    result = await agent.run("hello world")
    assert result == "Hello from the agent."


@pytest.mark.asyncio
async def test_build_runtime_preserves_card_temperature(tmp_registry: AgentRegistry, gateway):
    record = tmp_registry.create(name="temp-test", card=Card.HERMIT, model=_TEST_MODEL)
    agent = tmp_registry.build_runtime(record, gateway)
    await agent.run("test")
    call_args = gateway.complete.call_args[0][1]
    assert abs(call_args.temperature - 0.35) < 0.01


@pytest.mark.asyncio
async def test_create_persist_reload_build_run_end_to_end(tmp_registry: AgentRegistry, gateway):
    """Full round-trip: create → persist → reload from disk → build runtime → run."""
    original = tmp_registry.create(
        name="e2e-agent",
        card=Card.MAGICIAN,
        model=_TEST_MODEL,
        description="E2E test agent",
    )

    reloaded = tmp_registry.get(original.id)
    assert reloaded is not None
    assert reloaded.id == original.id
    assert reloaded.card == Card.MAGICIAN

    agent = tmp_registry.build_runtime(reloaded, gateway)
    result = await agent.run("end-to-end test")
    assert result == "Hello from the agent."

    sessions = agent._sessions
    assert len(sessions) == 1
    assert sessions[0].status == SessionStatus.COMPLETED
    roles = [m.role for m in sessions[0].messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


@pytest.mark.asyncio
async def test_build_runtime_with_modifier_cards_blends_temperature(tmp_registry: AgentRegistry, gateway):
    # Hermit (0.35) + Empress (0.85) modifier → ~0.50
    record = tmp_registry.create(
        name="blended",
        card=Card.HERMIT,
        model=_TEST_MODEL,
        modifier_cards=[Card.EMPRESS],
    )
    agent = tmp_registry.build_runtime(record, gateway)
    await agent.run("test blend")
    call_args = gateway.complete.call_args[0][1]
    assert abs(call_args.temperature - 0.50) < 0.01


# ---------------------------------------------------------------------------
# Legacy migration: model_connection_id → model
# ---------------------------------------------------------------------------


def test_legacy_record_migrates_model_connection_id(tmp_path):
    """An agent.json with model_connection_id (no model) loads with model='' by default."""
    import json as _json

    agent_dir = tmp_path / "agents" / "00000000-0000-0000-0000-000000000042"
    agent_dir.mkdir(parents=True)
    legacy_data = {
        "id": "00000000-0000-0000-0000-000000000042",
        "name": "legacy-agent",
        "card": "the-hermit",
        "model_connection_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "system_prompt": "You are legacy.",
        "temperature": 0.35,
    }
    (agent_dir / "agent.json").write_text(_json.dumps(legacy_data))

    registry = AgentRegistry(base_dir=tmp_path / "agents")
    record = registry.get(_json.loads(legacy_data["id"]) if False else __import__("uuid").UUID(legacy_data["id"]))
    assert record is not None
    # UUID can't be resolved → model set to ""
    assert record.model == ""
    assert record.name == "legacy-agent"
