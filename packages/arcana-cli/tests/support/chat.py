"""Shared set-up for the chat session tests: a scratch ``ARCANA_HOME``, a stand-in runtime and a controller.

The chat modules each read ``ARCANA_HOME`` from their own namespace, so
:func:`use_arcana_home` points all of them at one scratch directory. A test
module wraps these helpers in its own fixtures.
"""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import arcana_cli.commands.chat.app as chat_app
import arcana_cli.commands.chat.command as chat_command
import arcana_cli.commands.chat.controller as chat_controller
import arcana_cli.commands.run as run_mod
import arcana_cli.tui.app as tui_app
import arcana_cli.tui.history as tui_history
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.models.connection_store import ConnectionStore
from arcana.tools import MCPRegistry
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana.types.session import MessageRole, Session
from arcana_cli.commands.chat.controller import _ChatController
from arcana_cli.ui.renderer import Renderer
from tests.support.renderer import RecordingRenderer


def use_arcana_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch ``ARCANA_HOME`` with an Ollama connection, wired into every chat module."""
    home = tmp_path / ".arcana"
    for sub in ("agents", "connections"):
        (home / sub).mkdir(parents=True)
    for module in (chat_app, chat_command, run_mod, tui_app, tui_history):
        monkeypatch.setattr(module, "ARCANA_HOME", home)
    conn = ModelConnection(
        name="ollama/hermes-3",
        provider=ModelProvider.OLLAMA,
        default_model="hermes-3",
        endpoint="http://localhost:11434",
    )
    (home / "connections" / "models.json").write_text(json.dumps([json.loads(conn.model_dump_json())]))
    return home


def create_agent(
    home: Path, name: str = "scout", card: Card = Card.HERMIT, model: str = "ollama/hermes-3"
) -> AgentRecord:
    return AgentRegistry(home / "agents").create(name=name, card=card, model=model)


def mock_runtime(
    chunks: tuple[str, ...] | list[str] = ("Hi",),
    *,
    received: list[str] | None = None,
    error: Exception | None = None,
    gate: asyncio.Event | None = None,
    started: asyncio.Event | None = None,
) -> Any:
    """A stand-in runtime agent whose ``.stream`` yields ``chunks`` (or raises ``error``).

    With ``gate``, the stream yields the first chunk, sets ``started``, then
    waits for ``gate`` before yielding the rest, so a test can look at a reply
    mid-stream (or cancel it there).
    """

    async def _fake_stream(prompt: str, *, session: Session | None = None, context: str | None = None):
        if received is not None:
            received.append(prompt)
        if session is not None:
            session.add_message(MessageRole.USER, prompt)
        if error is not None:
            raise error
        for i, c in enumerate(chunks):
            yield c
            if i == 0 and gate is not None:
                if started is not None:
                    started.set()
                await gate.wait()
        if session is not None:
            session.add_message(MessageRole.ASSISTANT, "".join(chunks))

    rt = MagicMock()
    rt.name = "scout"  # real str — the reply eyebrow uppercases/escapes it
    rt.stream = _fake_stream
    rt.card_config = CardEngine(get_registry()).resolve(Card.HERMIT, [])
    return rt


def make_controller(
    home: Path,
    record: AgentRecord,
    runtime: Any,
    *,
    renderer: Renderer | None = None,
    federation: Any = None,
    memory_off: bool = False,
    session: Session | None = None,
) -> _ChatController:
    """A controller over ``runtime`` (a :class:`RecordingRenderer` by default), opened (header shown)."""
    sm = SessionManager(home / "agents")
    tools = MCPRegistry(connections_file=home / "connections" / "mcps.json")
    tools.load()
    controller = _ChatController(
        renderer=renderer if renderer is not None else RecordingRenderer(),
        reg=AgentRegistry(home / "agents"),
        gw=FakeGateway(),
        sm=sm,
        record=record,
        session=session if session is not None else sm.start(record.id),
        runtime_agent=runtime,
        federation=federation,
        memory_off=memory_off,
        connections=ConnectionStore(home / "connections" / "models.json"),
        tools=tools,
    )
    controller.open()
    return controller


def patch_build(monkeypatch: pytest.MonkeyPatch, runtime: Any, federation: Any = None) -> list[dict[str, Any]]:
    """Stub ``build_session_runtime`` everywhere the chat calls it; returns the kwargs of each call."""
    calls: list[dict[str, Any]] = []

    async def _fake(*args: Any, **kwargs: Any) -> tuple[Any, Any]:
        calls.append(kwargs)
        return runtime, federation

    monkeypatch.setattr(chat_controller, "build_session_runtime", _fake)
    monkeypatch.setattr(chat_app, "build_session_runtime", _fake)
    return calls


class FakeGateway:
    """An async-context stand-in for ``ModelGateway`` that opens no connections."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.closed = False

    async def __aenter__(self) -> "FakeGateway":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def aclose(self) -> None:
        """Drops the (nonexistent) cached adapters, as ``ModelGateway.aclose`` does."""
        self.closed = True


class FakeFederation:
    """Records whether the session closed its memory federation."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
