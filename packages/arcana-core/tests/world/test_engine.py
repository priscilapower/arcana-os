"""Tests for WorldEngine.route() — pool building, audit-before-exec, reflex
routing, tier gating, and the USER_RETRY learning signal."""

from datetime import timedelta
from pathlib import Path

import pytest

from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.types import (
    Agent,
    AgentStatus,
    CapabilityTier,
    Card,
    MessageRole,
    QualitySignalSource,
    ResolutionLayer,
    RoutingConfig,
    RoutingDecision,
    RoutingRule,
    SessionQualitySignal,
    SessionTrigger,
    Spread,
    SpreadLayout,
)
from arcana.types._utils import now_utc
from arcana.world.audit import RoutingAuditLog
from arcana.world.engine import WorldEngine
from arcana.world.learning import QualitySignalSink
from arcana.world.reflex import ReflexClassifier
from arcana.world.router import NoRouteAskUser
from arcana.world.store import LoadedWorld, WorldStore
from tests.support.model import RoutingModel, reflex_reply

REFLEX_MODEL = "ollama/reflex"


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


def _reply(agent: Agent, *, confidence: float = 0.8, reasoning: str = "best fit") -> str:
    return reflex_reply(agent.id, confidence=confidence, reasoning=reasoning)


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


class _SpySink:
    """Captures emitted quality signals; never raises."""

    def __init__(self) -> None:
        self.emitted: list[SessionQualitySignal] = []

    def emit(self, signal: SessionQualitySignal) -> None:
        self.emitted.append(signal)


class _RaisingSink:
    """A sink whose emit always fails — to prove emission is fire-and-forget."""

    def emit(self, signal: SessionQualitySignal) -> None:
        raise RuntimeError("sink is down")


def _engine(
    reg: AgentRegistry,
    *,
    loaded: LoadedWorld,
    audit: RoutingAuditLog | None = None,
    reflex: ReflexClassifier | None = None,
    sessions: SessionManager | None = None,
    signals: QualitySignalSink | None = None,
) -> WorldEngine:
    return WorldEngine(
        reg,
        store=_StubStore(loaded),
        audit=audit or _SpyAudit(),
        reflex=reflex,
        sessions=sessions,
        signals=signals,
    )


def _loaded(*, rules=None, config=None, spread=None) -> LoadedWorld:
    return LoadedWorld(rules=rules or [], config=config or RoutingConfig(), spread=spread)


# ---------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------


async def test_pool_falls_back_to_all_agents_when_no_spread(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="hi", target_agent_id=b.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    decision = await engine.route("hi there")
    assert decision.resolved_agent_id == b.id
    assert set(decision.candidate_pool) == {a.id, b.id}


async def test_pool_comes_from_active_spread(tmp_path):
    inside = _agent("inside")
    outside = _agent("outside")
    reg = _registry(tmp_path, inside, outside)
    spread = Spread(name="s", layout=SpreadLayout(positions={"role": inside.id}))
    engine = _engine(reg, loaded=_loaded(spread=spread))
    decision = await engine.route("anything")
    # only the spread member is a candidate → it's the sole default
    assert decision.candidate_pool == [inside.id]
    assert decision.resolved_agent_id == inside.id
    assert decision.spread_id == spread.id


async def test_archived_agent_is_excluded_from_pool(tmp_path):
    live = _agent("live")
    gone = _agent("gone", archived=True)
    reg = _registry(tmp_path, live, gone)
    engine = _engine(reg, loaded=_loaded())
    decision = await engine.route("hello")
    assert decision.candidate_pool == [live.id]


async def test_rule_cannot_route_to_archived_agent(tmp_path):
    live = _agent("live")
    gone = _agent("gone", archived=True)
    reg = _registry(tmp_path, live, gone)
    rule = RoutingRule(trigger="secret", target_agent_id=gone.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    # the rule targets an archived agent → skipped; live is sole default
    decision = await engine.route("secret mission")
    assert decision.resolved_agent_id == live.id
    assert decision.layer == ResolutionLayer.DEFAULT


async def test_reversed_agent_stays_selectable(tmp_path):
    rev = _agent("reversed-one", reversed_=True)
    reg = _registry(tmp_path, rev)
    rule = RoutingRule(trigger="go", target_agent_id=rev.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    decision = await engine.route("go now")
    assert decision.resolved_agent_id == rev.id
    assert decision.layer == ResolutionLayer.RULE


async def test_status_does_not_shrink_pool(tmp_path):
    """Agents default to IDLE; selectability is archival, not runtime status."""
    idle = _agent("idle")
    idle.status = AgentStatus.IDLE
    reg = _registry(tmp_path, idle)
    engine = _engine(reg, loaded=_loaded())
    decision = await engine.route("ping")
    assert decision.resolved_agent_id == idle.id


# ---------------------------------------------------------------------------
# Audit-before-exec, latency, trigger origin
# ---------------------------------------------------------------------------


async def test_decision_is_audited_before_returning(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    spy = _SpyAudit()
    engine = _engine(reg, loaded=_loaded(), audit=spy)
    decision = await engine.route("hi")
    assert spy.appended == [decision]  # audited, and it's the returned decision


async def test_ask_user_is_audited_before_raising(tmp_path):
    a = _agent("a")
    b = _agent("b")
    reg = _registry(tmp_path, a, b)
    spy = _SpyAudit()
    engine = _engine(reg, loaded=_loaded(), audit=spy)
    with pytest.raises(NoRouteAskUser) as excinfo:
        await engine.route("ambiguous")
    # the ask-user decision was recorded before the exception surfaced
    assert len(spy.appended) == 1
    assert spy.appended[0].resolved_agent_id is None
    assert excinfo.value.decision is spy.appended[0]


async def test_audit_failure_does_not_strand_route(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    # A real audit whose write fails is fail-open. Force failure by making the
    # target path a directory, so open(..., "a") raises — the route must proceed.
    audit_path = tmp_path / "world" / "routing_audit.jsonl"
    audit = RoutingAuditLog(audit_path)  # ctor creates the parent dir
    audit_path.mkdir()  # now a directory sits where the log file should be
    engine = WorldEngine(reg, store=_StubStore(_loaded()), audit=audit)
    decision = await engine.route("hi")  # must not raise
    assert decision.resolved_agent_id == a.id


async def test_latency_is_stamped(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    engine = _engine(reg, loaded=_loaded())
    decision = await engine.route("hi")
    assert decision.latency_ms >= 0


async def test_trigger_origin_is_recorded(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    engine = _engine(reg, loaded=_loaded())
    decision = await engine.route("hi", trigger_origin=SessionTrigger.WORLD)
    assert decision.trigger_origin == SessionTrigger.WORLD


async def test_explicit_agent_records_explicit_layer(tmp_path):
    a = _agent("a")
    b = _agent("b")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="anything", target_agent_id=a.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    decision = await engine.route("anything goes", explicit_agent=b)
    assert decision.layer == ResolutionLayer.EXPLICIT
    assert decision.resolved_agent_id == b.id
    assert decision.matched_rule_id is None


# ---------------------------------------------------------------------------
# Reflex layer
# ---------------------------------------------------------------------------


async def test_reflex_resolves_unmatched_task(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)  # two agents, no rule → would ask-user without reflex
    model = RoutingModel(content=_reply(b, confidence=0.9, reasoning="bravo wins"))
    engine = _engine(reg, loaded=_loaded(), reflex=ReflexClassifier(model, REFLEX_MODEL))
    decision = await engine.route("do the thing")
    assert decision.layer == ResolutionLayer.REFLEX
    assert decision.resolved_agent_id == b.id
    assert decision.reflex_confidence == 0.9
    assert decision.reflex_reasoning == "bravo wins"
    assert decision.reflex_model_id is not None
    assert decision.low_confidence is False


async def test_reflex_low_confidence_flag_is_set(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    model = RoutingModel(content=_reply(a, confidence=0.2))
    engine = _engine(reg, loaded=_loaded(), reflex=ReflexClassifier(model, REFLEX_MODEL))
    decision = await engine.route("hmm")
    assert decision.layer == ResolutionLayer.REFLEX
    assert decision.resolved_agent_id == a.id
    assert decision.low_confidence is True


async def test_reflex_failure_falls_through_to_default(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)  # sole candidate → a default exists
    model = RoutingModel(content="not json")  # classifier can't parse → None
    engine = _engine(reg, loaded=_loaded(), reflex=ReflexClassifier(model, REFLEX_MODEL))
    decision = await engine.route("please route")
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id == solo.id
    assert model.completions == 1  # it tried, then degraded


async def test_reflex_not_called_on_rule_match(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="invoice", target_agent_id=b.id)
    model = RoutingModel(content=_reply(a))
    engine = _engine(reg, loaded=_loaded(rules=[rule]), reflex=ReflexClassifier(model, REFLEX_MODEL))
    decision = await engine.route("please pay this invoice")
    assert decision.layer == ResolutionLayer.RULE
    assert decision.resolved_agent_id == b.id
    assert model.completions == 0  # no model call on a rule-covered route


async def test_reflex_not_called_on_explicit_route(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    model = RoutingModel(content=_reply(a))
    engine = _engine(reg, loaded=_loaded(), reflex=ReflexClassifier(model, REFLEX_MODEL))
    decision = await engine.route("anything", explicit_agent=b)
    assert decision.layer == ResolutionLayer.EXPLICIT
    assert model.completions == 0  # no model call on an explicit route


async def test_reflex_not_called_on_empty_task(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    model = RoutingModel(content=_reply(a))
    engine = _engine(reg, loaded=_loaded(), reflex=ReflexClassifier(model, REFLEX_MODEL))
    # An empty opening task carries nothing to classify; the classifier must not fire.
    with pytest.raises(NoRouteAskUser):
        await engine.route("")
    assert model.completions == 0


# ---------------------------------------------------------------------------
# Tier gating
# ---------------------------------------------------------------------------


async def test_no_model_tier_skips_reflex(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    model = RoutingModel(content=_reply(a))
    clf = ReflexClassifier(model, REFLEX_MODEL, tier=CapabilityTier.NO_MODEL)
    engine = _engine(reg, loaded=_loaded(), reflex=clf)
    with pytest.raises(NoRouteAskUser):  # no rule, ambiguous default, reflex gated off
        await engine.route("route me")
    assert model.completions == 0  # zero reflex calls at the no-model tier


async def test_no_reflex_classifier_makes_zero_model_calls(tmp_path):
    a = _agent("solo")
    reg = _registry(tmp_path, a)
    engine = _engine(reg, loaded=_loaded())  # no reflex wired at all
    decision = await engine.route("resolve me offline")
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id == a.id


async def test_route_is_deterministic(tmp_path):
    a = _agent("alpha")
    b = _agent("bravo")
    reg = _registry(tmp_path, a, b)
    rule = RoutingRule(trigger="ping", target_agent_id=b.id)
    engine = _engine(reg, loaded=_loaded(rules=[rule]))
    first = await engine.route("ping me")
    second = await engine.route("ping me")
    assert first.resolved_agent_id == second.resolved_agent_id == b.id
    assert first.candidate_pool == second.candidate_pool
    assert first.layer == second.layer


# ---------------------------------------------------------------------------
# USER_RETRY learning signal
# ---------------------------------------------------------------------------


def _seed_prior_session(sm: SessionManager, agent: Agent, prompt: str) -> None:
    """Persist a closed session whose opening user message is ``prompt``."""
    session = sm.start(agent.id)
    sm.append(session, MessageRole.USER, prompt)
    sm.close(session)


async def test_user_retry_emits_signal_on_repeat(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")
    _seed_prior_session(sm, solo, "reindex the docs")
    sink = _SpySink()
    engine = _engine(reg, loaded=_loaded(), sessions=sm, signals=sink)
    await engine.route("reindex the docs")
    assert len(sink.emitted) == 1
    signal = sink.emitted[0]
    assert signal.source == QualitySignalSource.USER_RETRY
    assert signal.value == -0.5
    assert signal.agent_id == solo.id


async def test_user_retry_caught_after_a_long_run(tmp_path):
    # The prior run's opening prompt is older than the window, but the run only
    # just finished — the window anchors to ended_at, so the retry still counts.
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")
    session = sm.start(solo.id)
    opening = sm.append(session, MessageRole.USER, "reindex the docs")
    opening.timestamp = now_utc() - timedelta(seconds=300)  # long run: prompt is 5 min old
    sm.close(session)  # ended_at = now → within the default 60s window
    sink = _SpySink()
    engine = _engine(reg, loaded=_loaded(), sessions=sm, signals=sink)
    await engine.route("reindex the docs")
    assert len(sink.emitted) == 1


async def test_user_retry_normalises_whitespace_and_case(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")
    _seed_prior_session(sm, solo, "Reindex The Docs")
    sink = _SpySink()
    engine = _engine(reg, loaded=_loaded(), sessions=sm, signals=sink)
    await engine.route("  reindex   the docs  ")
    assert len(sink.emitted) == 1


async def test_user_retry_not_emitted_for_different_prompt(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")
    _seed_prior_session(sm, solo, "summarise the meeting")
    sink = _SpySink()
    engine = _engine(reg, loaded=_loaded(), sessions=sm, signals=sink)
    await engine.route("reindex the docs")
    assert sink.emitted == []


async def test_user_retry_not_emitted_outside_window(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")
    _seed_prior_session(sm, solo, "reindex the docs")
    sink = _SpySink()
    # retry_window_s=0 → any elapsed time is "outside the window".
    engine = _engine(reg, loaded=_loaded(config=RoutingConfig(retry_window_s=0)), sessions=sm, signals=sink)
    await engine.route("reindex the docs")
    assert sink.emitted == []


async def test_user_retry_not_emitted_without_prior_session(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")  # no prior sessions on disk
    sink = _SpySink()
    engine = _engine(reg, loaded=_loaded(), sessions=sm, signals=sink)
    await engine.route("reindex the docs")
    assert sink.emitted == []


async def test_user_retry_signal_failure_does_not_block_route(tmp_path):
    solo = _agent("solo")
    reg = _registry(tmp_path, solo)
    sm = SessionManager(tmp_path / "agents")
    _seed_prior_session(sm, solo, "reindex the docs")
    engine = _engine(reg, loaded=_loaded(), sessions=sm, signals=_RaisingSink())
    decision = await engine.route("reindex the docs")  # sink raises — must be swallowed
    assert decision.resolved_agent_id == solo.id
