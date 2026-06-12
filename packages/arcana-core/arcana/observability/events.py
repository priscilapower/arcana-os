"""Typed audit log event dataclasses.

Phase 1 (implemented): SessionEvent, ModelCallEvent
Phase 1b stubs (not yet wired): RoutingEvent, MemoryReadEvent, MemoryWriteEvent
"""

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


# ---------------------------------------------------------------------------
# Phase 1b stubs — defined but not yet wired (World Engine / Memory not built)
# ---------------------------------------------------------------------------


@dataclass
class RoutingEvent:
    """Emitted by World Engine before routing each prompt. Not yet wired."""

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
    """Emitted by MemoryFederation on each retrieval. Not yet wired."""

    session_id: str
    agent_id: str
    query_text: str
    results_count: int
    latency_ms: int
    type: Literal["memory_read"] = field(default="memory_read", init=False)
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class MemoryWriteEvent:
    """Emitted by MemoryFederation on each write. Not yet wired."""

    session_id: str
    agent_id: str
    memory_type: str
    importance: float
    type: Literal["memory_write"] = field(default="memory_write", init=False)
    timestamp: str = field(default_factory=_now_iso)


AuditEvent = SessionEvent | ModelCallEvent | RoutingEvent | MemoryReadEvent | MemoryWriteEvent


def event_to_dict(event: AuditEvent) -> dict[str, Any]:
    return dataclasses.asdict(event)
