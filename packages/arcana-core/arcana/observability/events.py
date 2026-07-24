"""Typed audit log event dataclasses."""

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SessionEvent:
    """Emitted by Agent after each run() or stream() completes."""

    session_id: str
    agent_id: str
    agent_name: str
    card: str
    modifier_cards: list[str]
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    status: str
    type: Literal["session"] = field(default="session", init=False)
    timestamp: str = field(default_factory=_now_iso)
    cost: float | None = None


@dataclass
class ModelCallEvent:
    """Emitted by ModelGateway after each adapter call (including retries)."""

    session_id: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    attempt: int
    success: bool
    type: Literal["model_call"] = field(default="model_call", init=False)
    timestamp: str = field(default_factory=_now_iso)
    error: str | None = None


@dataclass
class RoutingEvent:
    """Emitted by the World Engine before routing each prompt."""

    session_id: str
    prompt_preview: str
    outcome: str
    matched_rule_trigger: str
    target_agent_name: str
    target_card: str
    rules_evaluated: int
    confidence: float
    duration_ms: int
    type: Literal["routing"] = field(default="routing", init=False)
    timestamp: str = field(default_factory=_now_iso)
    namespace_id: str = "local"
    workspace_id: str = "default"


@dataclass
class MemoryReadEvent:
    """Emitted on each memory retrieval."""

    session_id: str
    agent_id: str
    query_text: str
    results_count: int
    latency_ms: int
    type: Literal["memory_read"] = field(default="memory_read", init=False)
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class MemoryWriteEvent:
    """Emitted on each memory write."""

    session_id: str
    agent_id: str
    memory_type: str
    importance: float
    type: Literal["memory_write"] = field(default="memory_write", init=False)
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class MemoryPruneEvent:
    """Emitted after a memory prune pass over one store."""

    agent_id: str
    scanned: int
    archived: int
    purged: int
    type: Literal["memory_prune"] = field(default="memory_prune", init=False)
    timestamp: str = field(default_factory=_now_iso)


# Memory-degradation vocabulary — the single source of truth shared by
# MemoryDegradedEvent, TierWriteFailed.reason, and the resilience/federation
# paths that construct them.
MemoryOperation = Literal["read", "write", "promote"]
MemoryDegradeReason = Literal["timeout", "breaker_open", "backend_error", "corruption"]


@dataclass
class MemoryDegradedEvent:
    """Emitted when a memory tier is skipped or drops out of an operation.

    A degraded read returns partial context; a degraded shared/global write is
    dropped. Either way the session proceeds — this event is how the thinning is
    surfaced instead of being silent.
    """

    agent_id: str
    session_id: str
    tier: str  # "private" | "shared:{pool}" | "global"
    operation: MemoryOperation
    reason: MemoryDegradeReason
    message: str = ""
    type: Literal["memory_degraded"] = field(default="memory_degraded", init=False)
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class GuardrailViolationEvent:
    """Emitted when a tool call matches a guardrail rule.

    The audit half of the guardrail seam: a ``block`` match is recorded here and
    the tool never runs, while a ``warn`` / ``log`` match is recorded and the
    call proceeds. ``blocked`` is what tells the two apart after the fact.

    The call's arguments are deliberately **not** recorded. A ``write_file``
    violation would otherwise put the file's contents in the audit log, so the
    event carries the target path and nothing else from the payload.
    """

    agent_id: str
    tool_name: str
    rule_type: str
    severity: str
    reason: str
    blocked: bool
    target: str = ""
    description: str = ""
    type: Literal["guardrail_violation"] = field(default="guardrail_violation", init=False)
    timestamp: str = field(default_factory=_now_iso)


AuditEvent = (
    SessionEvent
    | ModelCallEvent
    | RoutingEvent
    | MemoryReadEvent
    | MemoryWriteEvent
    | MemoryPruneEvent
    | MemoryDegradedEvent
    | GuardrailViolationEvent
)


def event_to_dict(event: AuditEvent) -> dict[str, Any]:
    return dataclasses.asdict(event)
