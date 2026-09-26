"""A throwaway ``~/.arcana`` for command tests that cross command groups.

:func:`install_world` points every command module at a temp home, stubs the OS
keyring with a dict (so a test can check where a secret landed) and keeps MCP
discovery and OAuth probing offline. The ``seed_*`` helpers put the records a
scenario needs in place.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import pytest

import arcana_cli.commands.agent as agent_mod
import arcana_cli.commands.mcp as mcp_mod
import arcana_cli.commands.memory as memory_mod
import arcana_cli.commands.providers as providers_mod
import arcana_cli.commands.tools as tools_mod
from arcana.agents.registry import AgentRegistry
from arcana.memory import build_federation, paths
from arcana.models import ConnectionStore
from arcana.tools.registry import MCPRegistry
from arcana.types import MemoryEntry, MemoryType
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana.types.tool import MCPServerConfig, MCPServerStatus, ToolDefinition, ToolType

#: The id of the one memory :func:`seed_memory` writes.
MEMORY_ID = UUID("11111111-2222-3333-4444-555555555555")


@dataclass
class World:
    """The temp home, and the dict standing in for the OS keyring (reference → secret)."""

    root: Path
    keyring: dict[str, str] = field(default_factory=dict)

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def models(self) -> Path:
        return self.root / "connections" / "models.json"

    @property
    def mcps(self) -> Path:
        return self.root / "connections" / "mcps.json"


def install_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    root = tmp_path / ".arcana"
    (root / "agents").mkdir(parents=True, exist_ok=True)
    w = World(root)
    monkeypatch.setenv("HOME", str(tmp_path))
    for mod in (agent_mod, providers_mod, mcp_mod, memory_mod, tools_mod):
        monkeypatch.setattr(mod, "AGENTS_BASE", w.agents)
    monkeypatch.setattr(agent_mod, "CONNECTIONS_PATH", w.models)
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", w.models)
    monkeypatch.setattr(mcp_mod, "MCPS_PATH", w.mcps)
    monkeypatch.setattr(tools_mod, "MCPS_PATH", w.mcps)
    monkeypatch.setattr(memory_mod, "ARCANA_HOME", root)
    monkeypatch.setattr(memory_mod, "MEMORY_ADAPTERS_PATH", root / "connections" / "memory-adapters.json")
    monkeypatch.setattr(memory_mod, "_load_embedding_gateway", lambda: None)
    monkeypatch.setattr(paths, "_GUARDRAILS", paths.MemoryGuardrails(scope_paths=str(tmp_path)))

    async def no_probe(_url: str) -> None:
        return None

    monkeypatch.setattr(mcp_mod, "probe_oauth", no_probe)
    monkeypatch.setattr("arcana.tools.registry.MCPToolAdapter", FakeMCPAdapter)
    monkeypatch.setattr("keyring.set_password", lambda _svc, ref, value: w.keyring.__setitem__(ref, value))
    monkeypatch.setattr("keyring.get_password", lambda _svc, ref: w.keyring.get(ref))
    monkeypatch.setattr("keyring.delete_password", lambda _svc, ref: w.keyring.pop(ref, None))
    return w


class FakeMCPAdapter:
    """Discovers one canned tool and never connects."""

    def __init__(self, _cfg: MCPServerConfig) -> None:
        pass

    async def discover(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_pages",
                description="Search pages",
                input_schema={"type": "object", "properties": {}},
                type=ToolType.MCP,
                mcp_server_name="notion-mcp",
            )
        ]

    async def aclose(self) -> None:
        return None


def seed_connection(w: World, name: str, provider: ModelProvider, model: str, endpoint: str = "") -> None:
    ConnectionStore(w.models).upsert(
        ModelConnection(name=name, provider=provider, default_model=model, endpoint=endpoint)
    )


def seed_ollama(w: World) -> None:
    seed_connection(w, "ollama/hermes-3", ModelProvider.OLLAMA, "hermes-3", "http://localhost:11434")


def seed_agent(w: World, *, subs: list[str] | None = None) -> None:
    """An agent named ``scout`` on the Ollama connection, subscribed to ``subs``."""
    reg = AgentRegistry(w.agents)
    record = reg.create(name="scout", card=Card.HERMIT, model="ollama:ollama/hermes-3/hermes-3")
    reg.save(record.model_copy(update={"tool_subscriptions": subs or []}))


def seed_server(w: World) -> None:
    """A connected MCP server named ``notion-mcp``."""
    reg = MCPRegistry(connections_file=w.mcps)
    reg.load()
    reg.register_server(
        MCPServerConfig(name="notion-mcp", server_url="https://a/sse", status=MCPServerStatus.CONNECTED)
    )


def seed_memory(w: World) -> None:
    """An agent named ``hermit`` holding one private memory, :data:`MEMORY_ID`."""
    record = AgentRegistry(w.agents).create(name="hermit", card=Card.HERMIT, model="")

    async def write() -> None:
        fed = await build_federation(record.id, home=w.root)
        try:
            await fed.write(
                MemoryEntry(
                    id=MEMORY_ID,
                    agent_id=record.id,
                    type=MemoryType.SEMANTIC,
                    content="the tea is oolong",
                    importance=0.5,
                )
            )
        finally:
            await fed.aclose()

    asyncio.run(write())
