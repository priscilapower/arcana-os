"""
MCPRegistry — OS-level registry of all connected MCP servers and their tools.

This is a singleton owned by the OS, not by any individual agent.
Agents subscribe to tools from here by qualified name.

Connect once:
    arcana connect mcp --name notion --url https://mcp.notion.com/mcp
    # → discovers tools, stores in ~/.arcana/connections/mcps.json

Agents subscribe:
    agent.tool_subscriptions = ["notion-mcp/search_pages", "builtin/web_search"]

At session start, Agent asks the registry to resolve subscriptions
into ToolDefinitions, filtered by model capability.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from arcana.types.tool import (
    MCPServerConfig,
    ToolDefinition,
    ToolSubscription,
    ToolType,
)


class MCPRegistry:
    """
    Holds all MCP server configs and discovered tools.
    Loaded from ~/.arcana/connections/mcps.json at startup.
    """

    CONNECTIONS_FILE = Path.home() / ".arcana" / "connections" / "mcps.json"

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._builtins: dict[str, ToolDefinition] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load server configs from disk. Builtins are always registered."""
        self._register_builtins()
        if self.CONNECTIONS_FILE.exists():
            data = json.loads(self.CONNECTIONS_FILE.read_text())
            for entry in data.get("servers", []):
                server = MCPServerConfig(**entry)
                self._servers[server.name] = server
        self._loaded = True

    def save(self) -> None:
        """Persist server configs to disk (no secrets — those live in keyring)."""
        self.CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"servers": [s.model_dump(exclude={"status"}) for s in self._servers.values()]}
        self.CONNECTIONS_FILE.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_server(self, server: MCPServerConfig) -> None:
        """Register an MCP server. Called after `arcana connect mcp`."""
        self._servers[server.name] = server
        self.save()

    def remove_server(self, name: str) -> None:
        self._servers.pop(name, None)
        self.save()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def resolve(
        self,
        subscriptions: list[ToolSubscription],
        supports_tools: bool = True,
    ) -> list[ToolDefinition]:
        """
        Resolve an agent's tool subscriptions into ToolDefinitions.

        Called at session start. Returns only tools whose server is
        connected and whose model supports tool calling.
        """
        if not supports_tools:
            return []

        self._ensure_loaded()
        tools: list[ToolDefinition] = []

        for sub in subscriptions:
            tool = self._resolve_one(sub)
            if tool:
                tools.append(tool)

        return tools

    def list_servers(self) -> list[MCPServerConfig]:
        self._ensure_loaded()
        return list(self._servers.values())

    def list_all_tools(self) -> list[ToolDefinition]:
        """All tools across all connected servers + builtins."""
        self._ensure_loaded()
        tools = list(self._builtins.values())
        for server in self._servers.values():
            if server.status == "connected":
                tools.extend(server.discovered_tools)
        return tools

    def get_server(self, name: str) -> MCPServerConfig | None:
        self._ensure_loaded()
        return self._servers.get(name)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _resolve_one(self, sub: ToolSubscription) -> ToolDefinition | None:
        if sub.is_builtin:
            return self._builtins.get(sub.tool_name)

        server = self._servers.get(sub.server_name or "")
        if not server or server.status != "connected":
            return None

        return server.get_tool(sub.tool_name)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _register_builtins(self) -> None:
        """Register always-available built-in tools."""
        builtins = [
            ToolDefinition(
                name="web_search",
                description="Search the web for current information",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                type=ToolType.BUILTIN,
            ),
            ToolDefinition(
                name="fetch_url",
                description="Fetch the content of a URL",
                input_schema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
                type=ToolType.BUILTIN,
            ),
            ToolDefinition(
                name="read_file",
                description="Read a file from the local filesystem",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                type=ToolType.BUILTIN,
            ),
            ToolDefinition(
                name="write_file",
                description="Write content to a file on the local filesystem",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                type=ToolType.BUILTIN,
            ),
            ToolDefinition(
                name="run_code",
                description="Execute Python code in a sandboxed environment",
                input_schema={
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                    "required": ["code"],
                },
                type=ToolType.BUILTIN,
            ),
        ]
        for tool in builtins:
            self._builtins[tool.name] = tool


@lru_cache(maxsize=1)
def get_mcp_registry() -> MCPRegistry:
    """Global singleton. Use this everywhere."""
    registry = MCPRegistry()
    registry.load()
    return registry
