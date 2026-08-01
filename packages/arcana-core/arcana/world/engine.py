"""WorldEngine.route() — the orchestration around the pure router.

The engine gathers the routing inputs (the candidate pool from the active Spread
or all selectable agents, plus rules/config/Spread from the store), calls the
side-effect-free :func:`Router.resolve`, stamps the measured latency, writes the
:class:`RoutingDecision` to the append-only audit **before** the agent runs, and
returns it. When resolution can't pick an agent it raises
:class:`NoRouteAskUser` — but only *after* auditing the ask-user decision, so the
record exists either way.

Routing runs entirely offline: no model is consulted at any layer here.
"""

import time
from uuid import UUID

from arcana.agents.registry import AgentRegistry
from arcana.types import Agent, RoutingDecision, SessionTrigger, Spread
from arcana.world.audit import RoutingAuditLog
from arcana.world.config import DEFAULT_TASK_PREVIEW_CHARS
from arcana.world.router import NoRouteAskUser, Router
from arcana.world.store import WorldStore


class WorldEngine:
    """Resolves *which agent runs this* over the registered agents.

    Reversed agents are intentionally left in the candidate pool — the router
    selects them like any other; handling their reversed state is a separate
    concern that wraps the resolved agent, not this selection.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        store: WorldStore | None = None,
        audit: RoutingAuditLog | None = None,
        preview_chars: int = DEFAULT_TASK_PREVIEW_CHARS,
    ) -> None:
        self._registry = registry
        self._store = store or WorldStore()
        self._audit = audit or RoutingAuditLog()
        self._preview_chars = preview_chars

    def route(
        self,
        task: str,
        *,
        trigger_origin: SessionTrigger = SessionTrigger.USER,
        explicit_agent: Agent | None = None,
    ) -> RoutingDecision:
        """Resolve ``task`` to a :class:`RoutingDecision`, auditing it first.

        Raises :class:`NoRouteAskUser` (after the ask-user decision is audited)
        when no agent could be resolved and the caller must name one.
        """
        loaded = self._store.load()
        pool = self._candidate_pool(loaded.spread)

        start = time.perf_counter()
        decision = Router.resolve(
            task,
            pool=pool,
            rules=loaded.rules,
            spread=loaded.spread,
            config=loaded.config,
            explicit_agent=explicit_agent,
            trigger_origin=trigger_origin,
            preview_chars=self._preview_chars,
        )
        decision.latency_ms = int((time.perf_counter() - start) * 1000)

        # Audit before the agent runs — fail-open, so the route is never
        # stranded by a failed write.
        self._audit.append(decision)

        if decision.resolved_agent_id is None:
            raise NoRouteAskUser(decision)
        return decision

    def _candidate_pool(self, spread: Spread | None) -> list[Agent]:
        """The agents routing may select from.

        From the active Spread's positions when one is active, else every
        selectable (non-archived) agent. Archived agents are never included, so
        a rule or default can't route to one. Ordering is deterministic —
        (name, id) — so an identical config always yields an identical pool.
        """
        if spread is not None:
            agents: list[Agent] = []
            # dict.fromkeys dedupes repeated agent ids while preserving order.
            for agent_id in dict.fromkeys(spread.layout.positions.values()):
                record = self._registry.get(agent_id)
                if record is not None and not record.is_archived:
                    agents.append(record)
        else:
            agents = self._registry.list()  # already excludes archived
        return sorted(agents, key=lambda a: (a.name, self._sort_key(a.id)))

    @staticmethod
    def _sort_key(agent_id: UUID) -> str:
        return str(agent_id)
