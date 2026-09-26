"""World Engine types — WorldConfig, RoutingRule, Spread, SpreadLayout, and the
router's decision/config vocabulary (ResolutionLayer, RuleMatchMode,
RoutingDecision, RoutingConfig)."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc
from arcana.types.guardrails import GuardrailRule
from arcana.types.session import SessionTrigger


class CapabilityTier(StrEnum):
    """The model capability available to the World for a routing decision.

    The tier gates the reflex classifier: at ``NO_MODEL`` the World has no model
    to consult, so routing stays fully deterministic (no reflex call). At
    ``REFLEX`` or ``REASONING`` a reflex model is present and the classifier runs
    on it — routing is ambient, so it always uses the cheap reflex model even when
    a reasoning model is also configured.
    """

    NO_MODEL = "no_model"
    REFLEX = "reflex"
    REASONING = "reasoning"


class ResolutionLayer(StrEnum):
    """Which layer of the routing pipeline resolved a task to its agent.

    The World tries these in order and the first to resolve wins; the winning
    layer is stamped onto every :class:`RoutingDecision` so a route is
    inspectable after the fact.

    Being a ``StrEnum``, a member *is* its wire value — it serializes into the
    audit record as the bare string.
    """

    EXPLICIT = "explicit"  # a caller named the agent (--agent / /switch)
    RULE = "rule"  # a keyword/regex RoutingRule matched
    REFLEX = "reflex"  # a model-backed classifier chose the agent
    DEFAULT = "default"  # no rule fired; fell through to the default agent


class RuleMatchMode(StrEnum):
    """How a :class:`RoutingRule`'s ``trigger`` is tested against a task.

    ``keyword`` is a case-insensitive substring test — the safe, predictable
    default for hand-authored triggers. ``regex`` treats the trigger as a
    regular expression.
    """

    KEYWORD = "keyword"
    REGEX = "regex"


class WorldConfig(BaseModel):
    """The World's own configuration — the top layer of the guardrail hierarchy.

    ``system_guardrails`` are the operator's hard floor: they apply to every
    agent and no card default or per-agent rule can lift them. The gateway
    evaluates them ahead of an agent's own rules, so the two only ever compose
    into a narrower set.
    """

    system_guardrails: list[GuardrailRule] = []


class RoutingRule(BaseModel):
    """A rule that tells The World which agent to route a task to.

    Rules are evaluated in priority order (highest first) and the first match
    wins. ``match_mode`` selects how ``trigger`` is tested against the task —
    a case-insensitive keyword by default, or a regular expression. A disabled
    rule (``enabled=False``) is skipped without being deleted.
    """

    id: UUID = Field(default_factory=uuid4)
    trigger: str  # keyword like "invoice", or a regex when match_mode is REGEX
    target_agent_id: UUID
    priority: int = 0  # higher = evaluated first
    match_mode: RuleMatchMode = RuleMatchMode.KEYWORD
    enabled: bool = True
    description: str = ""
    created_at: datetime = Field(default_factory=now_utc)

    namespace_id: str = "local"


class SpreadLayout(BaseModel):
    """Maps named roles to agent UUIDs within a Spread."""

    positions: dict[str, UUID] = {}  # role_name → agent_id
    # e.g. {"researcher": <uuid>, "writer": <uuid>, "critic": <uuid>}


class Spread(BaseModel):
    """
    A named configuration of agents arranged for a specific purpose.
    The active Spread determines which agents The World routes tasks to.

    Example spreads: "writing-mode", "deep-research", "code-review-team"
    Automations can be saved as Spreads and re-activated on demand.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    layout: SpreadLayout = Field(default_factory=SpreadLayout)
    is_active: bool = False
    # A default agent scoped to this Spread. When set, it wins over the
    # world-level default during routing (see RoutingConfig).
    default_agent_id: UUID | None = None
    created_at: datetime = Field(default_factory=now_utc)

    namespace_id: str = "local"


class RoutingConfig(BaseModel):
    """World-level routing knobs, persisted in ``world.json``.

    ``default_agent_id`` is the fallback agent when no rule matches; the active
    Spread's own default (if any) takes precedence over it. ``retry_window_s``
    bounds how long a just-routed task may be re-routed to a different agent.
    """

    default_agent_id: UUID | None = None
    retry_window_s: int = 60


class ReflexPick(BaseModel):
    """The reflex classifier's structured choice over the candidate pool.

    One local reflex-model call ranks the pool and returns the best agent with a
    ``confidence`` (0.0–1.0) and a one-line ``reasoning``. The engine stamps
    these onto the :class:`RoutingDecision` (``reflex_confidence`` /
    ``reflex_reasoning``) and, when ``confidence`` is below the configured
    threshold, still uses the pick but flags it ``low_confidence``.
    """

    agent_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class RoutingDecision(BaseModel):
    """The record of a single routing resolution — *which* agent runs a task and
    *how* the World arrived at it.

    Written to an append-only audit **before** the resolved agent executes, so a
    route is inspectable even if the run never starts. Its field set is
    deliberately the shape a durable run-start checkpoint will reuse, so nothing
    here has to be reshaped when that lands. ``resolved_agent_id`` is ``None``
    only when the ``DEFAULT`` layer could not pick an agent and the caller must
    ask the user.

    The ``reflex_*`` fields and ``low_confidence`` are set only on a ``REFLEX``
    decision: ``reflex_model_id`` names the classifier model, ``reflex_confidence``
    and ``reflex_reasoning`` carry its structured pick, and ``low_confidence`` is
    ``True`` when the pick landed below the confidence threshold (the agent is
    still selected, but the route is surfaced as unsure).
    """

    id: UUID = Field(default_factory=uuid4)
    task_preview: str  # first N chars of the task
    resolved_agent_id: UUID | None
    layer: ResolutionLayer
    matched_rule_id: UUID | None = None
    candidate_pool: list[UUID] = []
    spread_id: UUID | None = None
    trigger_origin: SessionTrigger = SessionTrigger.USER
    reflex_model_id: UUID | None = None  # the classifier model, on a REFLEX-layer decision
    reflex_confidence: float | None = None  # the pick's confidence, on a REFLEX-layer decision
    reflex_reasoning: str | None = None  # the pick's one-line rationale, on a REFLEX-layer decision
    low_confidence: bool = False  # REFLEX pick below the confidence threshold
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=now_utc)

    namespace_id: str = "local"
    workspace_id: str = "default"
