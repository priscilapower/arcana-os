from uuid import UUID, uuid4

from arcana.types import (
    ResolutionLayer,
    RoutingConfig,
    RoutingDecision,
    RoutingRule,
    RuleMatchMode,
    SessionTrigger,
    Spread,
    SpreadLayout,
)


def test_routing_rule_defaults():
    rule = RoutingRule(trigger="when user asks about code", target_agent_id=uuid4())
    assert rule.priority == 0
    assert rule.description == ""
    assert rule.namespace_id == "local"
    assert rule.match_mode == RuleMatchMode.KEYWORD
    assert rule.enabled is True
    assert isinstance(rule.id, UUID)
    assert rule.created_at is not None


def test_routing_rule_match_mode_and_enabled():
    rule = RoutingRule(trigger=r"\bbug\b", target_agent_id=uuid4(), match_mode=RuleMatchMode.REGEX, enabled=False)
    assert rule.match_mode == RuleMatchMode.REGEX
    assert rule.enabled is False


def test_resolution_layer_and_match_mode_are_str_values():
    assert ResolutionLayer.EXPLICIT == "explicit"
    assert ResolutionLayer.RULE == "rule"
    assert ResolutionLayer.REFLEX == "reflex"
    assert ResolutionLayer.DEFAULT == "default"
    assert RuleMatchMode.KEYWORD == "keyword"
    assert RuleMatchMode.REGEX == "regex"


def test_routing_config_defaults():
    config = RoutingConfig()
    assert config.default_agent_id is None
    assert config.retry_window_s == 60


def test_routing_decision_defaults_and_serialization():
    agent_id = uuid4()
    decision = RoutingDecision(
        task_preview="do the thing",
        resolved_agent_id=agent_id,
        layer=ResolutionLayer.RULE,
    )
    assert decision.resolved_agent_id == agent_id
    assert decision.matched_rule_id is None
    assert decision.candidate_pool == []
    assert decision.trigger_origin == SessionTrigger.USER
    assert decision.namespace_id == "local"
    assert decision.workspace_id == "default"
    # StrEnum members serialize as their bare string value.
    dumped = decision.model_dump(mode="json")
    assert dumped["layer"] == "rule"
    assert dumped["trigger_origin"] == "user"
    # And the record round-trips through JSON unchanged.
    assert RoutingDecision.model_validate_json(decision.model_dump_json()) == decision


def test_routing_decision_ask_user_has_no_resolved_agent():
    decision = RoutingDecision(task_preview="ambiguous", resolved_agent_id=None, layer=ResolutionLayer.DEFAULT)
    assert decision.resolved_agent_id is None


def test_spread_default_agent_id():
    agent_id = uuid4()
    spread = Spread(name="s", default_agent_id=agent_id)
    assert spread.default_agent_id == agent_id
    assert Spread(name="none").default_agent_id is None


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
