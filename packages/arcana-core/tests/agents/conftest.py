"""Shared fixtures for agent tests."""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from arcana.agents.agent import Agent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.models.adapters.base import CompletionResponse, ModelChunk, ModelHealth
from arcana.types.card import Card


def _make_adapter(*, content: str = "Hello from the agent.") -> MagicMock:
    """Return a mock ModelAdapter that returns a canned response."""
    adapter = MagicMock()
    adapter.complete = AsyncMock(return_value=CompletionResponse(content=content, input_tokens=10, output_tokens=5))
    adapter.health_check = AsyncMock(return_value=ModelHealth(healthy=True, model_id="test"))

    async def _stream(_req) -> AsyncGenerator[ModelChunk, None]:
        for word in content.split():
            yield ModelChunk(text=word + " ")

    adapter.stream = _stream
    return adapter


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.fixture
def agent(adapter):
    return Agent(name="test-agent", card=Card.HERMIT, model=adapter)


@pytest.fixture
def tmp_registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(base_dir=tmp_path / "agents")


@pytest.fixture
def tmp_session_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(base_dir=tmp_path / "agents")


@pytest.fixture
def model_connection_id():
    return uuid4()
