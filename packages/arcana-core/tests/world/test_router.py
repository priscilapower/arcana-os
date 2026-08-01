"""Unit tests for the pure Router.resolve() — every branch, no I/O."""

from uuid import uuid4

from arcana.types import (
    Agent,
    Card,
    ResolutionLayer,
    RoutingConfig,
    RoutingDecision,
    RoutingRule,
    RuleMatchMode,
    SessionTrigger,
    Spread,
    SpreadLayout,
)
from arcana.world.config import DEFAULT_TASK_PREVIEW_CHARS
from arcana.world.router import Router


def _agent(name: str) -> Agent:
    return Agent(name=name, card=Card.FOOL, system_prompt="p", temperature=0.5)


def _resolve(
    task: str,
    *,
    pool: list[Agent] | None = None,
    rules: list[RoutingRule] | None = None,
    spread: Spread | None = None,
    config: RoutingConfig | None = None,
    explicit_agent: Agent | None = None,
    trigger_origin: SessionTrigger = SessionTrigger.USER,
    preview_chars: int = DEFAULT_TASK_PREVIEW_CHARS,
) -> RoutingDecision:
    return Router.resolve(
        task,
        pool=pool or [],
        rules=rules or [],
        spread=spread,
        config=config or RoutingConfig(),
        explicit_agent=explicit_agent,
        trigger_origin=trigger_origin,
        preview_chars=preview_chars,
    )


# ---------------------------------------------------------------------------
# L0 — explicit bypass
# ---------------------------------------------------------------------------


def test_explicit_agent_bypasses_rules_and_default():
    chosen = _agent("chosen")
    other = _agent("other")
    rule = RoutingRule(trigger="anything", target_agent_id=other.id)
    decision = _resolve("anything at all", pool=[other], rules=[rule], explicit_agent=chosen)
    assert decision.layer == ResolutionLayer.EXPLICIT
    assert decision.resolved_agent_id == chosen.id
    assert decision.matched_rule_id is None


def test_explicit_agent_need_not_be_in_pool():
    chosen = _agent("chosen")
    decision = _resolve("hi", pool=[], explicit_agent=chosen)
    assert decision.resolved_agent_id == chosen.id
    assert decision.layer == ResolutionLayer.EXPLICIT


# ---------------------------------------------------------------------------
# L1 — rules
# ---------------------------------------------------------------------------


def test_keyword_rule_matches_case_insensitively():
    coder = _agent("coder")
    rule = RoutingRule(trigger="Python", target_agent_id=coder.id)
    decision = _resolve("help me with python please", pool=[coder], rules=[rule])
    assert decision.layer == ResolutionLayer.RULE
    assert decision.resolved_agent_id == coder.id
    assert decision.matched_rule_id == rule.id


def test_keyword_rule_no_match_falls_through_to_default():
    coder = _agent("coder")
    rule = RoutingRule(trigger="python", target_agent_id=coder.id)
    # sole candidate → default resolves to it, but via DEFAULT not RULE
    decision = _resolve("write me a poem", pool=[coder], rules=[rule])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id == coder.id


def test_regex_rule_matches():
    coder = _agent("coder")
    rule = RoutingRule(trigger=r"\bbug\b", target_agent_id=coder.id, match_mode=RuleMatchMode.REGEX)
    decision = _resolve("there is a bug here", pool=[coder], rules=[rule])
    assert decision.layer == ResolutionLayer.RULE
    assert decision.matched_rule_id == rule.id


def test_regex_rule_no_partial_word_match():
    coder = _agent("coder")
    other = _agent("other")
    rule = RoutingRule(trigger=r"\bbug\b", target_agent_id=coder.id, match_mode=RuleMatchMode.REGEX)
    # "debugger" must not match \bbug\b; two candidates → no default → ask user
    decision = _resolve("open the debugger", pool=[coder, other], rules=[rule])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id is None


def test_invalid_regex_is_treated_as_no_match():
    coder = _agent("coder")
    other = _agent("other")
    rule = RoutingRule(trigger="([unclosed", target_agent_id=coder.id, match_mode=RuleMatchMode.REGEX)
    decision = _resolve("([unclosed literally here", pool=[coder, other], rules=[rule])
    # malformed regex never routes and never raises
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id is None


def test_empty_trigger_never_matches():
    coder = _agent("coder")
    other = _agent("other")
    empty = RoutingRule(trigger="   ", target_agent_id=coder.id, priority=99)
    # a blank keyword trigger must not capture every task (two candidates → ask user)
    decision = _resolve("literally anything", pool=[coder, other], rules=[empty])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id is None


def test_empty_task_never_matches_a_rule():
    # A permissive regex rule must NOT fire on an empty task (the chat-startup
    # sentinel) — such a task resolves by default, not by rule.
    coder = _agent("coder")
    other = _agent("other")
    greedy = RoutingRule(trigger=".*", target_agent_id=coder.id, match_mode=RuleMatchMode.REGEX, priority=99)
    decision = _resolve("", pool=[coder, other], rules=[greedy])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id is None


def test_empty_regex_trigger_never_matches():
    coder = _agent("coder")
    other = _agent("other")
    empty = RoutingRule(trigger="", target_agent_id=coder.id, match_mode=RuleMatchMode.REGEX, priority=99)
    decision = _resolve("anything", pool=[coder, other], rules=[empty])
    assert decision.resolved_agent_id is None


def test_higher_priority_rule_wins():
    a = _agent("a")
    b = _agent("b")
    low = RoutingRule(trigger="deploy", target_agent_id=a.id, priority=1)
    high = RoutingRule(trigger="deploy", target_agent_id=b.id, priority=10)
    decision = _resolve("time to deploy", pool=[a, b], rules=[low, high])
    assert decision.resolved_agent_id == b.id
    assert decision.matched_rule_id == high.id


def test_first_rule_wins_on_equal_priority():
    a = _agent("a")
    b = _agent("b")
    first = RoutingRule(trigger="deploy", target_agent_id=a.id, priority=5)
    second = RoutingRule(trigger="deploy", target_agent_id=b.id, priority=5)
    decision = _resolve("deploy now", pool=[a, b], rules=[first, second])
    assert decision.matched_rule_id == first.id


def test_disabled_rule_is_skipped():
    a = _agent("a")
    b = _agent("b")
    disabled = RoutingRule(trigger="deploy", target_agent_id=a.id, priority=10, enabled=False)
    enabled = RoutingRule(trigger="deploy", target_agent_id=b.id, priority=1)
    decision = _resolve("deploy", pool=[a, b], rules=[disabled, enabled])
    assert decision.resolved_agent_id == b.id
    assert decision.matched_rule_id == enabled.id


def test_rule_targeting_agent_outside_pool_is_skipped():
    in_pool = _agent("in")
    out_of_pool = _agent("out")
    rule = RoutingRule(trigger="x", target_agent_id=out_of_pool.id)
    # only `in_pool` is selectable; the rule's target isn't → skipped → default
    decision = _resolve("x marks it", pool=[in_pool], rules=[rule])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id == in_pool.id


# ---------------------------------------------------------------------------
# L3 — default resolution
# ---------------------------------------------------------------------------


def test_spread_default_wins_over_world_default():
    a = _agent("a")
    b = _agent("b")
    spread = Spread(name="s", layout=SpreadLayout(positions={"x": a.id, "y": b.id}), default_agent_id=a.id)
    config = RoutingConfig(default_agent_id=b.id)
    decision = _resolve("no rule matches", pool=[a, b], spread=spread, config=config)
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id == a.id


def test_world_default_used_when_no_spread_default():
    a = _agent("a")
    b = _agent("b")
    config = RoutingConfig(default_agent_id=b.id)
    decision = _resolve("nothing matches", pool=[a, b], config=config)
    assert decision.resolved_agent_id == b.id


def test_default_outside_pool_is_ignored():
    a = _agent("a")
    b = _agent("b")
    ghost = uuid4()
    config = RoutingConfig(default_agent_id=ghost)
    # two candidates, default not selectable → ask user rather than guess
    decision = _resolve("meh", pool=[a, b], config=config)
    assert decision.resolved_agent_id is None


def test_sole_candidate_is_the_default():
    only = _agent("only")
    decision = _resolve("whatever", pool=[only])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id == only.id


def test_ambiguous_pool_asks_user():
    a = _agent("a")
    b = _agent("b")
    decision = _resolve("ambiguous", pool=[a, b])
    assert decision.layer == ResolutionLayer.DEFAULT
    assert decision.resolved_agent_id is None


def test_empty_pool_asks_user():
    decision = _resolve("nobody home", pool=[])
    assert decision.resolved_agent_id is None


# ---------------------------------------------------------------------------
# Decision record shape & determinism
# ---------------------------------------------------------------------------


def test_decision_records_candidate_pool_and_preview():
    a = _agent("a")
    b = _agent("b")
    decision = _resolve("a long task description here", pool=[a, b], preview_chars=6)
    assert decision.candidate_pool == [a.id, b.id]
    assert decision.task_preview == "a long"


def test_decision_carries_trigger_origin_and_spread_id():
    a = _agent("a")
    spread = Spread(name="s", layout=SpreadLayout(positions={"x": a.id}), default_agent_id=a.id)
    decision = _resolve("x", pool=[a], spread=spread, trigger_origin=SessionTrigger.WORLD)
    assert decision.trigger_origin == SessionTrigger.WORLD
    assert decision.spread_id == spread.id


def test_resolution_is_deterministic():
    a = _agent("a")
    b = _agent("b")
    rule = RoutingRule(trigger="ping", target_agent_id=b.id)
    first = _resolve("ping me", pool=[a, b], rules=[rule])
    second = _resolve("ping me", pool=[a, b], rules=[rule])
    assert (first.resolved_agent_id, first.layer, first.matched_rule_id, first.candidate_pool) == (
        second.resolved_agent_id,
        second.layer,
        second.matched_rule_id,
        second.candidate_pool,
    )
