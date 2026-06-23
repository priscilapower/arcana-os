"""Agent and related types."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc
from arcana.types.card import Card


class AgentStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    ERROR = "error"
    SLEEPING = "sleeping"


class Agent(BaseModel):
    """An agent definition. Persisted to ``~/.arcana/agents/{id}/agent.json``."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""

    # Identity — the soul
    card: Card
    modifier_cards: list[Card] = []

    # Brain — set by CardEngine, user can override system_prompt
    model: str = ""  # provider[:name]/model_id reference, e.g. "anthropic/claude-sonnet-4-6"
    system_prompt: str
    temperature: float

    # Tool subscriptions — format: "server_name/tool_name" or "builtin/tool_name"
    tool_subscriptions: list[str] = []

    skill_ids: list[str] = []
    shared_pool_names: list[str] = []

    # State
    status: AgentStatus = AgentStatus.IDLE
    last_active: datetime | None = None
    is_reversed: bool = False

    # Meta
    created_at: datetime = Field(default_factory=now_utc)
    tags: list[str] = []
    is_archived: bool = False
    namespace_id: str = "local"
