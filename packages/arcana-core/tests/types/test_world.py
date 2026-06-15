from uuid import UUID, uuid4

from arcana.types import RoutingRule, Spread, SpreadLayout


def test_routing_rule_defaults():
    rule = RoutingRule(trigger="when user asks about code", target_agent_id=uuid4())
    assert rule.priority == 0
    assert rule.description == ""
    assert rule.namespace_id == "local"
    assert isinstance(rule.id, UUID)
    assert rule.created_at is not None


def test_routing_rule_priority():
    rule = RoutingRule(trigger="code question", target_agent_id=uuid4(), priority=10)
    assert rule.priority == 10


def test_routing_rule_namespace_id():
    rule = RoutingRule(trigger="code question", target_agent_id=uuid4(), namespace_id="team-alpha")
    assert rule.namespace_id == "team-alpha"


def test_spread_layout_defaults():
    layout = SpreadLayout()
    assert layout.positions == {}


def test_spread_layout_with_positions():
    researcher = uuid4()
    writer = uuid4()
    layout = SpreadLayout(positions={"researcher": researcher, "writer": writer})
    assert layout.positions["researcher"] == researcher
    assert layout.positions["writer"] == writer


def test_spread_defaults():
    spread = Spread(name="writing-mode")
    assert spread.description == ""
    assert spread.is_active is False
    assert spread.namespace_id == "local"
    assert isinstance(spread.layout, SpreadLayout)
    assert spread.layout.positions == {}
    assert isinstance(spread.id, UUID)
    assert spread.created_at is not None


def test_spread_activation():
    spread = Spread(name="deep-research", is_active=True)
    assert spread.is_active is True


def test_spread_with_layout():
    researcher = uuid4()
    spread = Spread(
        name="research-team",
        layout=SpreadLayout(positions={"researcher": researcher}),
    )
    assert spread.layout.positions["researcher"] == researcher


def test_spread_namespace_id():
    spread = Spread(name="team-spread", namespace_id="team-beta")
    assert spread.namespace_id == "team-beta"
