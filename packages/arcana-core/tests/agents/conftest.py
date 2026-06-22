"""Shared fixtures for agent tests."""

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcana.agents.agent import Agent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.models.adapters.base import CompletionResponse, ModelChunk
from arcana.models.gateway import ModelGateway
from arcana.types.card import Card


def _make_gateway(*, content: str = "Hello from the agent.") -> MagicMock:
    """Return a mock ModelGateway with canned responses."""
    gw = MagicMock(spec=ModelGateway)
    gw.complete = AsyncMock(return_value=CompletionResponse(content=content, input_tokens=10, output_tokens=5))

    words = content.split()

    async def _stream(_model: str, _req: object) -> AsyncGenerator[ModelChunk, None]:
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            yield ModelChunk(
                text=word + " ",
                input_tokens=10 if is_last else 0,
                output_tokens=5 if is_last else 0,
            )

    gw.stream = _stream
    return gw


@pytest.fixture
def gateway():
    return _make_gateway()


@pytest.fixture
def agent(gateway):
    return Agent(name="test-agent", card=Card.HERMIT, gateway=gateway, model="ollama/test-model")


@pytest.fixture
def tmp_registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(base_dir=tmp_path / "agents")


@pytest.fixture
def tmp_session_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(base_dir=tmp_path / "agents")
