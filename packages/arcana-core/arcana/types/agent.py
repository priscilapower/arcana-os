"""Agent and related types."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc
from arcana.types.card import Card


class AgentStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    ERROR = "error"
    SLEEPING = "sleeping"


class Agent(BaseModel):
    """
    An agent definition. Persisted to ~/.arcana/agents/{id}/agent.json.

    Tools:
      Agents do NOT own MCP connections. They subscribe to tools from
      the OS-level MCPRegistry via qualified names ("notion-mcp/search_pages").
      MCP servers are registered once via `arcana connect mcp` and shared
      across all agents. No duplicated configuration.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""

    # Identity — the soul
    card: Card
    modifier_cards: list[Card] = []

    # Brain — set by CardEngine, user can override system_prompt
    model_connection_id: UUID
    system_prompt: str
    temperature: float

    # Tools — subscriptions to OS-level MCPRegistry tools
    # Format: "server_name/tool_name" or "builtin/tool_name"
    # e.g. ["notion-mcp/search_pages", "builtin/web_search"]
    tool_subscriptions: list[str] = []

    # Skills
    skill_ids: list[str] = []

    # Memory config (resolved at runtime by MemoryFederation)
    shared_pool_names: list[str] = []  # which SharedMemoryPools to join

    # State
    status: AgentStatus = AgentStatus.IDLE
    last_active: datetime | None = None
    is_reversed: bool = False

    # Meta
    created_at: datetime = Field(default_factory=now_utc)
    tags: list[str] = []
    is_archived: bool = False

    # Cloud — workspace scoping (always "local" in Phase 1/2)
    workspace_id: str = "local"
