"""Tests for WorldEngine.route() — pool building, audit-before-exec, no-model."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arcana.agents.registry import AgentRegistry
from arcana.types import (
    Agent,
    AgentStatus,
    Card,
    ResolutionLayer,
    RoutingConfig,
    RoutingDecision,
    RoutingRule,
    SessionTrigger,
    Spread,
    SpreadLayout,
)
from arcana.world.audit import RoutingAuditLog
from arcana.world.engine import WorldEngine
from arcana.world.router import NoRouteAskUser
from arcana.world.store import LoadedWorld, WorldStore


def _agent(name: str, *, archived: bool = False, reversed_: bool = False) -> Agent:
    return Agent(
        name=name,
        card=Card.FOOL,
        system_prompt="p",
        temperature=0.5,
        is_archived=archived,
        is_reversed=reversed_,
    )


def _registry(tmp_path: Path, *agents: Agent) -> AgentRegistry:
    reg = AgentRegistry(tmp_path / "agents")
    for agent in agents:
        reg.save(agent)
    return reg


class _StubStore(WorldStore):
    """A WorldStore returning fixed inputs, no filesystem."""

    def __init__(self, loaded: LoadedWorld) -> None:
        self._loaded = loaded

    def load(self) -> LoadedWorld:
        return self._loaded


class _SpyAudit(RoutingAuditLog):
    """Records every appended decision in call order; writes nothing."""

    def __init__(self) -> None:
        self.appended: list[RoutingDecision] = []

    def append(self, decision: RoutingDecision) -> None:
        self.appended.append(decision)


def _engine(reg: AgentRegistry, *, loaded: LoadedWorld, audit: RoutingAuditLog | None = None) -> WorldEngine:
    return WorldEngine(reg, store=_StubStore(loaded), audit=audit or _SpyAudit())


def _loaded(*, rules=None, config=None, spread=None) -> LoadedWorld:
    return LoadedWorld(rules=rules or [], config=config or RoutingConfig(), spread=spread)


# ---------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------


def test_pool_falls_back_to_all_agents_when_no_spread(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="hi", target_agent_id=b.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    decision = engine.route("hi there")
    assert decision.resolved_agent_id == b.id
    assert set(decision.candidate_pool) == {a.id, b.id}


def test_pool_comes_from_active_spread(tmp_path):
    inside = _agent("inside")
    outside = _agent("outside")
    reg = _registry(tmp_path, inside, outside)
    spread = Spread(name="s", layout=SpreadLayout(positions={"role": inside.id}))
    engine = _engine(reg, loaded=_loaded(spread=spread))
    decision = engine.route("anything")
    # only the spread member is a candidate → it's the sole default
    assert decision.candidate_pool == [inside.id]
    assert decision.resolved_agent_id == inside.id
    assert decision.spread_id == spread.id


def test_archived_agent_is_excluded_from_pool(tmp_path):
    live = _agent("live")
    gone = _agent("gone", archived=True)
    reg = _registry(tmp_path, live, gone)
    engine = _engine(reg, loaded=_loaded())
    decision = engine.route("hello")
    assert decision.candidate_pool == [live.id]


def test_rule_cannot_route_to_archived_agent(tmp_path):
    live = _agent("live")
    gone = _agent("gone", archived=True)
    reg = _registry(tmp_path, live, gone)
    rule = RoutingRule(trigger="secret", target_agent_id=gone.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    # the rule targets an archived agent → skipped; live is sole default
    decision = engine.route("secret mission")
    assert decision.resolved_agent_id == live.id
    assert decision.layer == ResolutionLayer.DEFAULT


def test_reversed_agent_stays_selectable(tmp_path):
    rev = _agent("reversed-one", reversed_=True)
    reg = _registry(tmp_path, rev)
    rule = RoutingRule(trigger="go", target_agent_id=rev.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    decision = engine.route("go now")
    assert decision.resolved_agent_id == rev.id
    assert decision.layer == ResolutionLayer.RULE


def test_status_does_not_shrink_pool(tmp_path):
    """Agents default to IDLE; selectability is archival, not runtime status."""
    idle = _agent("idle")
    idle.status = AgentStatus.IDLE
    reg = _registry(tmp_path, idle)
    engine = _engine(reg, loaded=_loaded())
    decision = engine.route("ping")
    assert decision.resolved_agent_id == idle.id


# ---------------------------------------------------------------------------
# Audit-before-exec, latency, trigger origin
# ---------------------------------------------------------------------------


def test_decision_is_audited_before_returning(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    spy = _SpyAudit()
    engine = _engine(reg, loaded=_loaded(), audit=spy)
    decision = engine.route("hi")
    assert spy.appended == [decision]  # audited, and it's the returned decision


def test_ask_user_is_audited_before_raising(tmp_path):
    a = _agent("a")
    b = _agent("b")
    reg = _registry(tmp_path, a, b)
    spy = _SpyAudit()
    engine = _engine(reg, loaded=_loaded(), audit=spy)
    with pytest.raises(NoRouteAskUser) as excinfo:
        engine.route("ambiguous")
    # the ask-user decision was recorded before the exception surfaced
    assert len(spy.appended) == 1
    assert spy.appended[0].resolved_agent_id is None
    assert excinfo.value.decision is spy.appended[0]


def test_audit_failure_does_not_strand_route(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    # A real audit whose write fails is fail-open. Force failure by making the
    # target path a directory, so open(..., "a") raises — the route must proceed.
    audit_path = tmp_path / "world" / "routing_audit.jsonl"
    audit = RoutingAuditLog(audit_path)  # ctor creates the parent dir
    audit_path.mkdir()  # now a directory sits where the log file should be
    engine = WorldEngine(reg, store=_StubStore(_loaded()), audit=audit)
    decision = engine.route("hi")  # must not raise
    assert decision.resolved_agent_id == a.id


def test_latency_is_stamped(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    engine = _engine(reg, loaded=_loaded())
    decision = engine.route("hi")
    assert decision.latency_ms >= 0


def test_trigger_origin_is_recorded(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    engine = _engine(reg, loaded=_loaded())
    decision = engine.route("hi", trigger_origin=SessionTrigger.WORLD)
    assert decision.trigger_origin == SessionTrigger.WORLD


def test_explicit_agent_records_explicit_layer(tmp_path):
    a = _agent("a")
    b = _agent("b")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="anything", target_agent_id=a.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    decision = engine.route("anything goes", explicit_agent=b)
    assert decision.layer == ResolutionLayer.EXPLICIT
    assert decision.resolved_agent_id == b.id
    assert decision.matched_rule_id is None


# ---------------------------------------------------------------------------
# No-model tier & determinism
# ---------------------------------------------------------------------------


def test_route_makes_zero_model_calls(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    gateway = MagicMock()
    engine = _engine(reg, loaded=_loaded())
    engine.route("resolve me offline")
    gateway.assert_not_called()  # the engine never had, nor used, a model gateway


def test_route_is_deterministic(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="ping", target_agent_id=b.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    first = engine.route("ping me")
    second = engine.route("ping me")
    assert first.resolved_agent_id == second.resolved_agent_id == b.id
    assert first.candidate_pool == second.candidate_pool
    assert first.layer == second.layer
