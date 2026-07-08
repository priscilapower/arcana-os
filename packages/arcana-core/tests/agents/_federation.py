"""Shared types for the Agent ↔ federation integration fixtures.

The ``make_federated_agent`` *fixture* lives in ``conftest.py`` (so pytest
auto-discovers it), but the types it returns and its call signature are reusable
across tests. Keeping them here lets a test import the types directly instead of
reaching into ``conftest``.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from unittest.mock import MagicMock
from uuid import UUID

from arcana.agents.agent import Agent
from arcana.memory import EmbeddingGateway, MemoryFederation
from arcana.memory.extraction import MemoryExtractor
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.observability import MemoryDegradedEvent
from arcana.types import MemoryAdapter


@dataclass
class FederatedAgent:
    """An ``Agent`` wired to a real assembled federation, with test hooks.

    ``degraded`` is the sink the federation reports degraded SHARED/GLOBAL tiers
    to, so a test can assert a ``MemoryDegradedEvent`` was emitted without going
    through the audit log. ``home`` is the ``~/.arcana`` root the private store
    lives under, for direct on-disk assertions.
    """

    agent: Agent
    federation: MemoryFederation
    gateway: MagicMock
    home: Path
    agent_id: UUID
    degraded: list[MemoryDegradedEvent] = field(default_factory=list)


class MakeFederatedAgent(Protocol):
    """The async factory a test calls to build a federated agent for its scenario."""

    async def __call__(
        self,
        *,
        embedding: EmbeddingGateway | EmbeddingAdapter | None = None,
        private: MemoryAdapter | None = None,
        global_: MemoryAdapter | None = None,
        extractor: MemoryExtractor | None = None,
        summarise_on_close: bool = True,
        min_confidence_for_context: float = 0.5,
        content: str = "Hello from the agent.",
    ) -> FederatedAgent: ...
