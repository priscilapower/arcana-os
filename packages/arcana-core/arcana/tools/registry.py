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

import json
from functools import lru_cache
from pathlib import Path

from arcana.tools.adapters.mcp import MCPToolAdapter, diff_discovered
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.types.tool import (
    MCPServerConfig,
    MCPServerStatus,
    ToolDefinition,
    ToolStatus,
    ToolSubscription,
    ToolType,
)

# Server states whose tools are eligible for resolution. A CHANGED server has
# at least one flagged tool but its unchanged tools still resolve normally.
_RESOLVABLE_STATUSES = frozenset({MCPServerStatus.CONNECTED, MCPServerStatus.CHANGED})


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
        """Persist server configs to disk (no secrets).

        The discovered-tool cache and the server's last-known ``status`` are
        persisted so schema injection stays connection-free across restarts —
        a server last seen ``connected`` reloads resolvable, without paying a
        connect cost just to see its tools. Auth material lives only in the
        keyring (``auth_key_ref`` is a reference, never the token).
        """
        self.CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"servers": [s.model_dump() for s in self._servers.values()]}
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
            if server.status in _RESOLVABLE_STATUSES:
                tools.extend(server.discovered_tools)
        return tools

    async def discover(self, cfg: MCPServerConfig) -> MCPServerConfig:
        """Connect to ``cfg``, list its tools, diff, persist, and register it.

        The live session is used only here; runtime schema injection reads the
        persisted ``discovered_tools``. Re-discovery flags rug-pulled tools as
        ``CHANGED`` (see :func:`diff_discovered`), which ``resolve`` then
        withholds. A server that fails to connect is persisted ``unreachable``
        with its last-known tools intact — never raising.
        """
        adapter = MCPToolAdapter(cfg)
        try:
            fresh = await adapter.discover()
        except Exception:
            cfg.status = MCPServerStatus.UNREACHABLE
            self._servers[cfg.name] = cfg
            self.save()
            return cfg
        finally:
            await adapter.aclose()

        cfg.discovered_tools = diff_discovered(cfg.discovered_tools, fresh)
        has_changed = any(t.status is ToolStatus.CHANGED for t in cfg.discovered_tools)
        cfg.status = MCPServerStatus.CHANGED if has_changed else MCPServerStatus.CONNECTED
        self._servers[cfg.name] = cfg
        self.save()
        return cfg

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
        if not server or server.status not in _RESOLVABLE_STATUSES:
            return None

        tool = server.get_tool(sub.tool_name)
        # Withhold rug-pulled tools: a mutated third-party schema must never be
        # injected into the model until it is re-approved.
        if tool is None or tool.status is not ToolStatus.ACTIVE:
            return None
        return tool

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _register_builtins(self) -> None:
        """Register always-available built-in tools.

        ``web_search`` / ``fetch_url`` come from the shared ``BUILTIN_DEFINITIONS``
        the ``BuiltinToolAdapter`` executes, so the model-visible and executable
        schemas cannot drift. The filesystem/exec builtins are declaration-only:
        the model can see their schemas but no adapter executes them yet.
        """
        for definition in BUILTIN_DEFINITIONS.values():
            self._builtins[definition.name] = definition

        builtins = [
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
