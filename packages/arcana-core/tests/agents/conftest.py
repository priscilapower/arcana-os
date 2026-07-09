"""Shared fixtures for agent tests."""

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest

from arcana.agents.agent import Agent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.memory import EmbeddingGateway, MemoryFederation, MemoryRouter, build_federation
from arcana.memory.extraction import MemoryExtractor
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.observability import MemoryDegradedEvent
from arcana.types import MemoryAdapter
from arcana.types.card import Card
from tests.agents._federation import FederatedAgent, MakeFederatedAgent
from tests.support.fakes import make_gateway


@pytest.fixture
def gateway():
    return make_gateway()


@pytest.fixture
def agent(gateway):
    return Agent(name="test-agent", card=Card.HERMIT, gateway=gateway, model="ollama/test-model")


@pytest.fixture
def tmp_registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(base_dir=tmp_path / "agents")


@pytest.fixture
def tmp_session_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(base_dir=tmp_path / "agents")


# ---------------------------------------------------------------------------
# Real-federation fixtures (Agent ↔ federation integration)
# ---------------------------------------------------------------------------


@pytest.fixture
async def make_federated_agent(tmp_path: Path) -> AsyncGenerator[MakeFederatedAgent, None]:
    """Factory: build an ``Agent`` over a *real* federation, closed on teardown.

    By default the federation is assembled through the production
    :func:`build_federation` seam (private SQLite, optional GLOBAL vector tier via
    ``embedding``), so the test path and the runtime path construct memory
    identically. For failure-injection scenarios a test passes pre-built
    ``private``/``global_`` adapters, which are composed directly through a
    :class:`MemoryRouter` (the only way to substitute a failing tier).
    """
    federations: list[MemoryFederation] = []

    async def _make(
        *,
        embedding: EmbeddingGateway | EmbeddingAdapter | None = None,
        private: MemoryAdapter | None = None,
        global_: MemoryAdapter | None = None,
        extractor: MemoryExtractor | None = None,
        summarise_on_close: bool = True,
        min_confidence_for_context: float = 0.5,
        content: str = "Hello from the agent.",
    ) -> FederatedAgent:
        agent_id = uuid4()
        degraded: list[MemoryDegradedEvent] = []
        gateway = make_gateway(content=content)

        if private is not None or global_ is not None:
            # Direct construction — the seam that lets us drop in a failing tier.
            assert private is not None, "direct construction needs a private tier"
            router = MemoryRouter(private=private, global_=global_)
            federation = MemoryFederation(router, on_degraded=degraded.append)
        else:
            if isinstance(embedding, EmbeddingGateway):
                embed_gateway: EmbeddingGateway | None = embedding
            elif embedding is not None:
                embed_gateway = EmbeddingGateway([embedding])
            else:
                embed_gateway = None
            federation = await build_federation(
                agent_id, home=tmp_path, embedding=embed_gateway, on_degraded=degraded.append
            )
        federations.append(federation)

        agent = Agent(
            name="fed-agent",
            card=Card.HERMIT,
            gateway=gateway,
            model="ollama/test-model",
            memory=federation,
            id=agent_id,
            session_manager=SessionManager(base_dir=tmp_path / "agents"),
            extractor=extractor,
            summarise_on_close=summarise_on_close,
            min_confidence_for_context=min_confidence_for_context,
        )
        return FederatedAgent(
            agent=agent,
            federation=federation,
            gateway=gateway,
            home=tmp_path,
            agent_id=agent_id,
            degraded=degraded,
        )

    yield _make

    for federation in federations:
        await federation.aclose()
