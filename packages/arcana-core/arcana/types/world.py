"""World Engine types — RoutingRule, Spread, SpreadLayout."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc


class RoutingRule(BaseModel):
    """A rule that tells The World which agent to route a task to.

    Written in natural language; rules are evaluated in priority order and the
    first match wins.
    """

    id: UUID = Field(default_factory=uuid4)
    trigger: str  # "when user asks about code"
    target_agent_id: UUID
    priority: int = 0  # higher = evaluated first
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
    created_at: datetime = Field(default_factory=now_utc)

    namespace_id: str = "local"
