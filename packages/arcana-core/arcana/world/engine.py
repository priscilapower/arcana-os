"""WorldEngine.route() — the orchestration around the pure router.

The engine gathers the routing inputs (the candidate pool from the active Spread
or all selectable agents, plus rules/config/Spread from the store), calls the
side-effect-free :func:`Router.resolve`, and — when no rule matched and a reflex
model is available — runs the reflex classifier over the pool before accepting
the deterministic default. It stamps the measured latency, writes the
:class:`RoutingDecision` to the append-only audit **before** the agent runs, and
returns it. When resolution can't pick an agent it raises :class:`NoRouteAskUser`
— but only *after* auditing the ask-user decision, so the record exists either way.

The deterministic layers (explicit / rule / default) consult no model. A model is
consulted only by the reflex classifier, and only when no rule matched and the
capability tier is at least reflex — never on an explicit or rule-covered route.
"""

import logging
import time
from uuid import UUID

from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.types import (
    Agent,
    CapabilityTier,
    MessageRole,
    QualitySignalSource,
    ResolutionLayer,
    RoutingDecision,
    SessionQualitySignal,
    SessionTrigger,
    Spread,
)
from arcana.types._utils import now_utc
from arcana.world.audit import RoutingAuditLog
from arcana.world.config import DEFAULT_TASK_PREVIEW_CHARS
from arcana.world.learning import QualitySignalSink
from arcana.world.reflex import ReflexClassifier
from arcana.world.router import NoRouteAskUser, Router
from arcana.world.store import WorldStore

logger = logging.getLogger("arcana.world.engine")

# The value carried by a USER_RETRY quality signal (a repeated prompt is a cheap
# proxy for a bad route). A named constant, not a knob: it is the signal's
# defined weight in the learning loop, not an operator tunable.
_USER_RETRY_VALUE = -0.5


class WorldEngine:
    """Resolves *which agent runs this* over the registered agents.

    Reversed agents are intentionally left in the candidate pool — the router
    selects them like any other; handling their reversed state is a separate
    concern that wraps the resolved agent, not this selection.

    The deterministic spine (explicit / rule / default) always runs. Passing a
    ``reflex`` classifier enables the model-backed reflex layer between rules and
    the default; passing ``sessions`` + ``signals`` enables the ``USER_RETRY``
    learning signal. Omitting any of them simply disables that behaviour — the
    engine stays fully functional and offline.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        *,
        store: WorldStore | None = None,
        audit: RoutingAuditLog | None = None,
        reflex: ReflexClassifier | None = None,
        sessions: SessionManager | None = None,
        signals: QualitySignalSink | None = None,
        preview_chars: int = DEFAULT_TASK_PREVIEW_CHARS,
    ) -> None:
        self._registry = registry
        self._store = store or WorldStore()
        self._audit = audit or RoutingAuditLog()
        self._reflex = reflex
        self._sessions = sessions
        self._signals = signals
        self._preview_chars = preview_chars

    async def route(
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
        # Reflex layer — only when no rule matched (a DEFAULT decision), the task
        # carries text, and the tier is at least reflex. Explicit and rule routes
        # never reach a model call.
        if self._should_try_reflex(decision, task):
            decision = await self._apply_reflex(decision, task, pool)
        decision.latency_ms = int((time.perf_counter() - start) * 1000)

        # Audit before the agent runs — fail-open, so the route is never
        # stranded by a failed write.
        self._audit.append(decision)

        # A repeated prompt feeds the learning loop; strictly fire-and-forget, so
        # it runs after the decision is settled and never affects the route.
        self._maybe_emit_retry(task, decision.resolved_agent_id, loaded.config.retry_window_s)

        if decision.resolved_agent_id is None:
            raise NoRouteAskUser(decision)
        return decision

    # ------------------------------------------------------------------
    # Reflex layer
    # ------------------------------------------------------------------

    def _should_try_reflex(self, decision: RoutingDecision, task: str) -> bool:
        """Whether the reflex classifier should run for this decision.

        It fires only when the deterministic pipeline fell through to the default
        (no explicit agent, no rule matched), the task has text to classify, and a
        reflex classifier at a model-bearing tier is configured — so explicit and
        rule-covered routes never reach a model call.
        """
        return (
            decision.layer == ResolutionLayer.DEFAULT
            and bool(task.strip())
            and self._reflex is not None
            and self._reflex.tier is not CapabilityTier.NO_MODEL
        )

    async def _apply_reflex(self, decision: RoutingDecision, task: str, pool: list[Agent]) -> RoutingDecision:
        """Run the classifier and, on a pick, upgrade the decision to REFLEX.

        A ``None`` pick (empty pool, timeout, malformed reply, out-of-pool id)
        leaves the deterministic default untouched — the fail-safe fall-through.
        """
        assert self._reflex is not None  # guarded by _should_try_reflex
        pick = await self._reflex.classify(task, pool)
        if pick is None:
            return decision
        return decision.model_copy(
            update={
                "resolved_agent_id": pick.agent_id,
                "layer": ResolutionLayer.REFLEX,
                "matched_rule_id": None,
                "reflex_model_id": self._reflex.model_connection_id,
                "reflex_confidence": pick.confidence,
                "reflex_reasoning": pick.reasoning,
                "low_confidence": pick.confidence < self._reflex.confidence_min,
            }
        )

    # ------------------------------------------------------------------
    # USER_RETRY learning signal
    # ------------------------------------------------------------------

    def _maybe_emit_retry(self, task: str, agent_id: UUID | None, retry_window_s: int) -> None:
        """Emit a ``USER_RETRY`` signal if ``task`` repeats the last prompt.

        Fires when the resolved agent's immediately prior session opened with the
        same prompt (normalised) and that session ended within ``retry_window_s``
        of now — measured from when the prior run finished, so a re-issue right
        after a long run still counts (it falls back to the opening message's time
        for a session that never closed). Strictly fire-and-forget: it needs both
        a session source and a signal sink, and any failure is swallowed so the
        route is never blocked.
        """
        if self._sessions is None or self._signals is None or agent_id is None:
            return
        if not task.strip():
            return
        try:
            prior = self._sessions.latest_session(agent_id)
            if prior is None:
                return
            opening = next((m for m in prior.messages if m.role == MessageRole.USER), None)
            if opening is None or _normalise(opening.content) != _normalise(task):
                return
            # Anchor the window to when the prior run finished (ended_at), so a
            # retry after a run longer than the window is still caught; a session
            # that never closed falls back to its opening message's timestamp.
            since = prior.ended_at or opening.timestamp
            if (now_utc() - since).total_seconds() > retry_window_s:
                return
            self._signals.emit(
                SessionQualitySignal(
                    source=QualitySignalSource.USER_RETRY,
                    value=_USER_RETRY_VALUE,
                    agent_id=agent_id,
                    task_preview=task[: self._preview_chars],
                )
            )
        except Exception:  # noqa: BLE001 — signal detection must never strand a route
            logger.warning("USER_RETRY detection failed; skipping signal", exc_info=True)

    # ------------------------------------------------------------------
    # Candidate pool
    # ------------------------------------------------------------------

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


def _normalise(text: str) -> str:
    """Collapse whitespace and lowercase, so trivial edits still count as a retry."""
    return " ".join(text.split()).lower()
