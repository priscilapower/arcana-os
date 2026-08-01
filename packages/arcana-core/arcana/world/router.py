"""Pure task selection — the deterministic core of the World's router.

:func:`Router.resolve` is side-effect-free: given a task and the routing inputs
(candidate pool, rules, active Spread, config, and any explicitly-named agent) it
returns a :class:`RoutingDecision` and touches no filesystem, model, or session.
That keeps every branch unit-testable without mocks, and makes routing
deterministic — identical inputs always yield the same decision.

The pipeline tries three selections in order and the first to resolve wins;
the winning stage is recorded as the decision's :class:`ResolutionLayer`:

* **Explicit** — a caller named the agent (``--agent`` / ``/switch``); routing is
  skipped and that agent is selected directly.
* **Rule** — the first enabled rule (priority desc) whose target is in the
  candidate pool and whose trigger matches the task.
* **Default** — the active Spread's default, else the world default, else the
  sole candidate; if none applies, ``resolved_agent_id`` is ``None`` and the
  caller must ask the user.
"""

import logging
import re
from uuid import UUID

from arcana.types import (
    Agent,
    ResolutionLayer,
    RoutingConfig,
    RoutingDecision,
    RoutingRule,
    RuleMatchMode,
    SessionTrigger,
    Spread,
)
from arcana.world.config import DEFAULT_TASK_PREVIEW_CHARS

logger = logging.getLogger("arcana.world.router")


class NoRouteAskUser(Exception):
    """Raised when routing cannot pick an agent and the user must name one.

    Carries the :class:`RoutingDecision` (with ``resolved_agent_id is None``)
    that was already audited, so callers can surface the same record they log.
    """

    def __init__(self, decision: RoutingDecision) -> None:
        self.decision = decision
        super().__init__("No agent could be resolved for the task; ask the user to name one.")


def _rule_matches(task: str, rule: RoutingRule) -> bool:
    """Whether ``rule`` fires for ``task`` under its match mode.

    Keyword mode is a case-insensitive substring test — forgiving for
    hand-authored triggers. Regex mode treats the trigger as a pattern (author
    controls case via inline flags). A malformed regex never routes and never
    raises — the rule is treated as a non-match so a bad pattern can't strand a
    route.

    A rule never fires on an empty (or whitespace-only) task or trigger: an empty
    substring — or a permissive regex like ``.*`` against an empty task — would
    otherwise match indiscriminately, letting a broad rule capture a task that
    carries no text to classify. Such a task resolves by default instead.
    """
    if not task.strip() or not rule.trigger.strip():
        return False
    if rule.match_mode == RuleMatchMode.REGEX:
        try:
            return re.search(rule.trigger, task) is not None
        except re.error:
            logger.warning("routing rule %s has an invalid regex trigger; skipping", rule.id)
            return False
    return rule.trigger.lower() in task.lower()


class Router:
    """Pure selection over the routing inputs. No I/O, no model, no persistence."""

    @staticmethod
    def resolve(
        task: str,
        *,
        pool: list[Agent],
        rules: list[RoutingRule],
        spread: Spread | None,
        config: RoutingConfig,
        explicit_agent: Agent | None = None,
        trigger_origin: SessionTrigger = SessionTrigger.USER,
        preview_chars: int = DEFAULT_TASK_PREVIEW_CHARS,
    ) -> RoutingDecision:
        """Resolve ``task`` to a :class:`RoutingDecision` (never raises)."""
        pool_ids = [agent.id for agent in pool]
        pool_id_set = set(pool_ids)
        spread_id = spread.id if spread is not None else None

        def decide(
            *,
            resolved: UUID | None,
            layer: ResolutionLayer,
            matched_rule_id: UUID | None = None,
        ) -> RoutingDecision:
            return RoutingDecision(
                task_preview=task[:preview_chars],
                resolved_agent_id=resolved,
                layer=layer,
                matched_rule_id=matched_rule_id,
                candidate_pool=pool_ids,
                spread_id=spread_id,
                trigger_origin=trigger_origin,
            )

        # Explicit — an explicitly named agent bypasses rule/default resolution.
        if explicit_agent is not None:
            return decide(resolved=explicit_agent.id, layer=ResolutionLayer.EXPLICIT)

        # Rule — first enabled rule (priority desc) whose target is selectable and
        # whose trigger matches. sorted() is stable, so equal priorities keep the
        # rules' given order — the decision stays deterministic.
        for rule in sorted(rules, key=lambda r: r.priority, reverse=True):
            if not rule.enabled or rule.target_agent_id not in pool_id_set:
                continue
            if _rule_matches(task, rule):
                return decide(
                    resolved=rule.target_agent_id,
                    layer=ResolutionLayer.RULE,
                    matched_rule_id=rule.id,
                )

        # Default — Spread default → world default → sole candidate. Each default
        # must be a selectable agent (in the pool), so a default pointing at an
        # archived/absent agent is never routed to.
        default_id = None
        if spread is not None and spread.default_agent_id in pool_id_set:
            default_id = spread.default_agent_id
        elif config.default_agent_id in pool_id_set:
            default_id = config.default_agent_id
        elif len(pool_ids) == 1:
            default_id = pool_ids[0]

        return decide(resolved=default_id, layer=ResolutionLayer.DEFAULT)
