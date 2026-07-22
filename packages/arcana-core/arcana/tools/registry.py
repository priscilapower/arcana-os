"""
MCPRegistry — OS-level registry of all connected MCP servers and their tools.

This is a singleton owned by the OS, not by any individual agent.
Agents subscribe to tools from here by qualified name.

Connect once:
    arcana mcp add --name notion --url https://mcp.notion.com/mcp
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

    def __init__(self, connections_file: Path | None = None) -> None:
        # Path injection mirrors ConnectionStore / AgentRegistry so a caller (the
        # CLI, tests) can point the registry at an ARCANA_HOME other than the
        # default ~/.arcana; omitted, it falls back to the module default.
        self.connections_file = connections_file or MCPRegistry.CONNECTIONS_FILE
        self._servers: dict[str, MCPServerConfig] = {}
        self._builtins: dict[str, ToolDefinition] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load server configs from disk. Builtins are always registered."""
        self._register_builtins()
        if self.connections_file.exists():
            data = json.loads(self.connections_file.read_text())
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
        self.connections_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"servers": [s.model_dump() for s in self._servers.values()]}
        self.connections_file.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_server(self, server: MCPServerConfig) -> None:
        """Register an MCP server. Called after `arcana mcp add`."""
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
        seen: set[str] = set()

        for sub in subscriptions:
            resolved = self._resolve_wildcard(sub) if sub.is_wildcard else self._resolve_singleton(sub)
            for tool in resolved:
                # Dedup so a `server/*` wildcard plus an explicit `server/tool`
                # for the same tool yields it once.
                if tool.qualified_name not in seen:
                    seen.add(tool.qualified_name)
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

    def approve(self, name: str, tool_names: list[str] | None = None) -> list[str]:
        """Re-approve a server's ``CHANGED`` tools, admitting their new metadata.

        This is the human side of the default-closed diff gate: a tool whose
        third-party ``description``/``input_schema`` mutated since it was first
        trusted is flagged ``CHANGED`` and withheld from resolution until it is
        approved here. Approving flips it back to ``ACTIVE`` — accepting its
        current schema as the new trusted baseline — and recomputes the server's
        status to ``CONNECTED`` once no ``CHANGED`` tool remains (a status only
        recomputed for an already-resolvable server, so approving never
        fabricates connectivity for an unreachable one). ``tool_names`` (bare
        tool names) narrows the set; ``None`` approves every changed tool.
        Returns the names actually flipped. Unknown server → ``KeyError``; names
        that are absent or not ``CHANGED`` are left untouched.
        """
        self._ensure_loaded()
        server = self._servers.get(name)
        if server is None:
            raise KeyError(name)

        wanted = set(tool_names) if tool_names is not None else None
        approved: list[str] = []
        updated: list[ToolDefinition] = []
        for tool in server.discovered_tools:
            if tool.status is ToolStatus.CHANGED and (wanted is None or tool.name in wanted):
                updated.append(tool.model_copy(update={"status": ToolStatus.ACTIVE}))
                approved.append(tool.name)
            else:
                updated.append(tool)

        if approved:
            server.discovered_tools = updated
            if server.status in _RESOLVABLE_STATUSES:
                still_changed = any(t.status is ToolStatus.CHANGED for t in updated)
                server.status = MCPServerStatus.CHANGED if still_changed else MCPServerStatus.CONNECTED
            self.save()
        return approved

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _resolve_singleton(self, sub: ToolSubscription) -> list[ToolDefinition]:
        """A single subscription resolved to zero or one tool."""
        tool = self._resolve_one(sub)
        return [tool] if tool else []

    def _resolve_wildcard(self, sub: ToolSubscription) -> list[ToolDefinition]:
        """Every ACTIVE tool of a ``server/*`` subscription's server.

        Withheld (``CHANGED``) tools are excluded, and an unresolvable server
        (unreachable / disconnected) contributes nothing — the same fail-closed
        posture as an explicit per-tool subscription.
        """
        server = self._servers.get(sub.server_name or "")
        if not server or server.status not in _RESOLVABLE_STATUSES:
            return []
        return [t for t in server.discovered_tools if t.status is ToolStatus.ACTIVE]

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
