"""
Typed event dataclasses for the Arcana audit log.

Every observability event is a plain dataclass — no Pydantic, no heavy deps.
Events are serialised to JSONL via dataclasses.asdict().

Add new events here as new subsystems come online. Keep events append-only:
adding fields is fine; removing or renaming fields breaks existing audit logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


def _now() -> str:
    return datetime.utcnow().isoformat()


def _uid() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

class RoutingOutcome(str, Enum):
    MATCHED  = "matched"    # a routing rule fired
    FALLBACK = "fallback"   # no rule matched; used default/fallback agent
    REJECTED = "rejected"   # no eligible agent could be found
    ERROR    = "error"      # routing itself raised an exception


@dataclass
class RoutingEvent:
    """
    One routing decision by The World.

    Written BEFORE the routed agent executes — so if the agent crashes,
    the routing decision is still visible in the audit log.

    Epic 7: populate matched_rule_id / confidence / rules_evaluated once
    The World's routing logic is built.
    """
    # Identity
    id: str                          = field(default_factory=_uid)
    timestamp: str                   = field(default_factory=_now)
    session_id: str                  = ""
    workspace_id: str                = "local"

    # Input
    prompt_preview: str              = ""   # first 200 chars — not the full prompt
    prompt_token_estimate: int       = 0

    # Decision
    outcome: str                     = RoutingOutcome.FALLBACK.value
    matched_rule_id: str | None      = None
    matched_rule_trigger: str | None = None  # natural-language rule that fired
    target_agent_id: str             = ""
    target_agent_name: str           = ""
    target_card: str                 = ""

    # Evaluation quality
    rules_evaluated: int             = 0    # number of rules The World considered
    confidence: float                = 0.0  # 0–1
    duration_ms: int                 = 0

    # Context
    active_spread_id: str | None     = None
    routing_model: str               = ""   # LLM used for routing decision


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@dataclass
class MemoryReadEvent:
    """Emitted each time MemoryFederation.search() completes."""
    id: str                    = field(default_factory=_uid)
    timestamp: str             = field(default_factory=_now)
    session_id: str            = ""
    agent_id: str              = ""
    workspace_id: str          = "local"

    query_preview: str         = ""     # first 100 chars of the query text
    tiers_searched: list[str]  = field(default_factory=list)
    # e.g. ["private", "shared:project-arcana", "global"]
    entries_returned: int      = 0
    entries_per_tier: dict     = field(default_factory=dict)
    # e.g. {"private": 3, "shared:project-arcana": 1, "global": 0}
    top_importance: float      = 0.0
    min_importance: float      = 0.0
    duration_ms: int           = 0
    budget_applied: bool       = False  # True if ContextBudget truncated a tier


@dataclass
class MemoryWriteEvent:
    """Emitted each time MemoryFederation.write() is called."""
    id: str                    = field(default_factory=_uid)
    timestamp: str             = field(default_factory=_now)
    session_id: str            = ""
    agent_id: str              = ""
    workspace_id: str          = "local"

    entry_id: str              = ""
    memory_type: str           = ""   # episodic | semantic | procedural | preference
    scope: str                 = ""   # private | shared | global
    pool_name: str | None      = None
    importance: float          = 0.0
    auto_promoted: bool        = False  # True if promoted to GLOBAL (importance >= 0.9)
    conflict_detected: bool    = False
    rejected: bool             = False  # True if below min_confidence threshold


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class ModelCallEvent:
    """Emitted after each LLM completion (streaming or not)."""
    id: str                    = field(default_factory=_uid)
    timestamp: str             = field(default_factory=_now)
    session_id: str            = ""
    agent_id: str              = ""
    workspace_id: str          = "local"

    model_id: str              = ""
    provider: str              = ""   # anthropic | ollama | openai
    input_tokens: int          = 0
    output_tokens: int         = 0
    cost_usd: float | None     = None  # None for local/self-hosted models
    duration_ms: int           = 0
    stop_reason: str           = ""
    streamed: bool             = False


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@dataclass
class ToolCallEvent:
    """Emitted after each tool execution by an agent."""
    id: str                    = field(default_factory=_uid)
    timestamp: str             = field(default_factory=_now)
    session_id: str            = ""
    agent_id: str              = ""
    workspace_id: str          = "local"

    tool_name: str             = ""   # "notion-mcp/search_pages" or "builtin/web_search"
    success: bool              = True
    error: str | None          = None
    duration_ms: int           = 0


# ---------------------------------------------------------------------------
# ContextBudget
# ---------------------------------------------------------------------------

@dataclass
class ContextBudgetEvent:
    """
    Emitted by SessionManager when ContextBudget enforcement runs.

    This is the primary diagnostic for "why did memory retrieval get truncated?"
    Powers `arcana world audit --budget`.
    """
    id: str                      = field(default_factory=_uid)
    timestamp: str               = field(default_factory=_now)
    session_id: str              = ""
    agent_id: str                = ""
    workspace_id: str            = "local"

    budget_tokens: int           = 0
    system_prompt_tokens: int    = 0
    global_memory_tokens: int    = 0
    shared_memory_tokens: int    = 0
    private_memory_tokens: int   = 0
    knowledge_tokens: int        = 0
    tiers_truncated: list[str]   = field(default_factory=list)
    # e.g. ["knowledge:obsidian-vault"] — which tiers were cut to fit the budget
    total_context_tokens: int    = 0


# ---------------------------------------------------------------------------
# soul.md
# ---------------------------------------------------------------------------

@dataclass
class SoulMdEvent:
    """Emitted each session when soul.md is read and injected."""
    id: str                    = field(default_factory=_uid)
    timestamp: str             = field(default_factory=_now)
    session_id: str            = ""
    workspace_id: str          = "local"

    token_count: int           = 0
    exceeds_threshold: bool    = False   # True if > 2,000 tokens
    threshold_tokens: int      = 2_000


# ---------------------------------------------------------------------------
# Registry — maps event type name → class (for typed log queries)
# ---------------------------------------------------------------------------

EVENT_TYPES: dict[str, type] = {
    "RoutingEvent":       RoutingEvent,
    "MemoryReadEvent":    MemoryReadEvent,
    "MemoryWriteEvent":   MemoryWriteEvent,
    "ModelCallEvent":     ModelCallEvent,
    "ToolCallEvent":      ToolCallEvent,
    "ContextBudgetEvent": ContextBudgetEvent,
    "SoulMdEvent":        SoulMdEvent,
}
