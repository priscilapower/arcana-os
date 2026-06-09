"""Tests for AgentRegistry."""

from uuid import uuid4

import pytest

from arcana.agents.registry import AgentRegistry
from arcana.types.card import Card


def test_create_returns_agent_record(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="researcher",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
    )
    assert record.name == "researcher"
    assert record.card == Card.HERMIT
    assert record.model_connection_id == model_connection_id


def test_create_resolves_system_prompt_from_card(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="researcher",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
    )
    assert "Researcher" in record.system_prompt


def test_create_resolves_temperature_from_card(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="researcher",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
    )
    assert abs(record.temperature - 0.35) < 0.01


def test_create_accepts_system_prompt_override(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="custom",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
        system_prompt_override="You are a pirate.",
    )
    assert record.system_prompt == "You are a pirate."


def test_create_persists_to_disk(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="saved",
        card=Card.FOOL,
        model_connection_id=model_connection_id,
    )
    agent_json = tmp_registry._base / str(record.id) / "agent.json"
    assert agent_json.exists()


def test_get_returns_record_by_id(tmp_registry: AgentRegistry, model_connection_id):
    created = tmp_registry.create(
        name="findme",
        card=Card.MAGICIAN,
        model_connection_id=model_connection_id,
    )
    found = tmp_registry.get(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.name == "findme"


def test_get_returns_none_for_unknown_id(tmp_registry: AgentRegistry):
    assert tmp_registry.get(uuid4()) is None


def test_list_returns_all_agents(tmp_registry: AgentRegistry, model_connection_id):
    for name in ("alpha", "beta", "gamma"):
        tmp_registry.create(
            name=name,
            card=Card.FOOL,
            model_connection_id=model_connection_id,
        )
    records = tmp_registry.list()
    assert len(records) == 3
    assert {r.name for r in records} == {"alpha", "beta", "gamma"}


def test_list_returns_empty_when_no_agents(tmp_registry: AgentRegistry):
    assert tmp_registry.list() == []


def test_list_sorts_by_name(tmp_registry: AgentRegistry, model_connection_id):
    for name in ("zebra", "apple", "mango"):
        tmp_registry.create(
            name=name,
            card=Card.FOOL,
            model_connection_id=model_connection_id,
        )
    names = [r.name for r in tmp_registry.list()]
    assert names == sorted(names)


def test_save_updates_existing_record(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="original",
        card=Card.FOOL,
        model_connection_id=model_connection_id,
    )
    updated = record.model_copy(update={"name": "updated"})
    tmp_registry.save(updated)

    found = tmp_registry.get(record.id)
    assert found is not None
    assert found.name == "updated"


def test_delete_soft_archives_agent(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="doomed",
        card=Card.TOWER,
        model_connection_id=model_connection_id,
    )
    tmp_registry.delete(record.id)

    found = tmp_registry.get(record.id)
    assert found is not None
    assert found.is_archived is True


def test_list_excludes_archived_agents(tmp_registry: AgentRegistry, model_connection_id):
    kept = tmp_registry.create(
        name="kept",
        card=Card.FOOL,
        model_connection_id=model_connection_id,
    )
    gone = tmp_registry.create(
        name="gone",
        card=Card.FOOL,
        model_connection_id=model_connection_id,
    )
    tmp_registry.delete(gone.id)

    records = tmp_registry.list()
    assert len(records) == 1
    assert records[0].id == kept.id


def test_delete_raises_for_unknown_id(tmp_registry: AgentRegistry):
    with pytest.raises(FileNotFoundError):
        tmp_registry.delete(uuid4())


def test_create_with_modifier_cards(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="blended",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
        modifier_cards=[Card.MAGICIAN],
    )
    assert Card.MAGICIAN in record.modifier_cards


def test_create_with_tags(tmp_registry: AgentRegistry, model_connection_id):
    record = tmp_registry.create(
        name="tagged",
        card=Card.FOOL,
        model_connection_id=model_connection_id,
        tags=["research", "phase-1"],
    )
    assert "research" in record.tags


def test_build_runtime_uses_record_id(tmp_registry: AgentRegistry, model_connection_id, gateway):
    record = tmp_registry.create(
        name="runtime-test",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
    )
    agent = tmp_registry.build_runtime(record, gateway, "ollama/test")
    assert agent.id == record.id


def test_build_runtime_uses_record_system_prompt(tmp_registry: AgentRegistry, model_connection_id, gateway):
    record = tmp_registry.create(
        name="rt",
        card=Card.HERMIT,
        model_connection_id=model_connection_id,
        system_prompt_override="Custom prompt.",
    )
    agent = tmp_registry.build_runtime(record, gateway, "ollama/test")
    assert agent._system_prompt == "Custom prompt."
