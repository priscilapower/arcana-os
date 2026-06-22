from uuid import UUID

from arcana.types import Agent, AgentStatus
from arcana.types.card import Card


def _make_agent(**kwargs) -> Agent:
    defaults = dict(
        name="test-agent",
        card=Card.HERMIT,
        model="anthropic/claude-test",
        system_prompt="You are a researcher.",
        temperature=0.35,
    )
    return Agent(**{**defaults, **kwargs})


def test_agent_status_values():
    assert AgentStatus.ACTIVE == "active"
    assert AgentStatus.IDLE == "idle"
    assert AgentStatus.ERROR == "error"
    assert AgentStatus.SLEEPING == "sleeping"


def test_agent_defaults():
    agent = _make_agent()
    assert agent.status == AgentStatus.IDLE
    assert agent.modifier_cards == []
    assert agent.tool_subscriptions == []
    assert agent.skill_ids == []
    assert agent.shared_pool_names == []
    assert agent.tags == []
    assert agent.is_reversed is False
    assert agent.is_archived is False
    assert agent.last_active is None
    assert agent.namespace_id == "local"
    assert agent.description == ""


def test_agent_id_is_uuid():
    agent = _make_agent()
    assert isinstance(agent.id, UUID)


def test_agent_with_modifiers():
    agent = _make_agent(modifier_cards=[Card.EMPRESS, Card.FOOL])
    assert Card.EMPRESS in agent.modifier_cards
    assert Card.FOOL in agent.modifier_cards


def test_agent_reversed():
    agent = _make_agent(is_reversed=True)
    assert agent.is_reversed is True


def test_agent_tool_subscriptions():
    agent = _make_agent(tool_subscriptions=["notion-mcp/search_pages", "builtin/web_search"])
    assert len(agent.tool_subscriptions) == 2
