"""Workspace type — namespace container for agents, spreads, and routing rules."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc


class Workspace(BaseModel):
    """
    A namespace that scopes agents, spreads, and routing rules.

    In Phase 1/2 only the built-in "local" workspace exists.
    Multi-tenant cloud workspaces are added in Phase 3 (Epic 7).
    The string `id` (slug) is what namespace_id fields on other models reference.
    """

    id: str  # slug — e.g. "local", "team-alpha"
    name: str
    description: str = ""
    owner_id: UUID | None = None  # None for the local workspace
    created_at: datetime = Field(default_factory=now_utc)
    is_default: bool = False
