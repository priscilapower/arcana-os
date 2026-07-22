"""Tool, Skill, MCP connection, and MCPRegistry types."""

from enum import StrEnum

from pydantic import BaseModel

from arcana.types._utils import JsonObject, JsonValue


class ToolType(StrEnum):
    BUILTIN = "builtin"
    MCP = "mcp"
    CUSTOM = "custom"


class ToolStatus(StrEnum):
    """Approval state of a discovered tool.

    ``ACTIVE`` tools resolve normally. ``CHANGED`` marks a tool whose
    description or input schema differs from the copy approved at first
    discovery — a potential rug-pull — and is withheld from resolution until
    re-approved, so mutated third-party metadata never reaches the model.
    """

    ACTIVE = "active"
    CHANGED = "changed"


class ToolDefinition(BaseModel):
    """Schema for a callable tool. Injected into agent context at session start."""

    name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject = {}
    type: ToolType = ToolType.BUILTIN
    mcp_server_name: str | None = None  # e.g. "notion-mcp"
    status: ToolStatus = ToolStatus.ACTIVE

    @property
    def qualified_name(self) -> str:
        """Fully qualified name: server_name/tool_name for MCP tools."""
        if self.mcp_server_name:
            return f"{self.mcp_server_name}/{self.name}"
        return self.name


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    output: JsonValue = None
    error: str | None = None
    duration_ms: int = 0


class Skill(BaseModel):
    """
    A higher-order capability — a bundle of tools + prompting strategy.
    Minor Arcana map to skill domains (Swords=analysis, Wands=creative, etc.)
    """

    id: str
    name: str
    description: str
    tarot_name: str | None = None  # e.g. "The Scribe", "The Sleuth"
    required_tool_names: list[str] = []  # qualified names e.g. "notion-mcp/search_pages"
    prompt_strategy: str = ""  # injected into system prompt when active
    affinity_cards: list[str] = []  # card ids that naturally fit this skill


class MCPTransport(StrEnum):
    SSE = "sse"
    STDIO = "stdio"
    WEBSOCKET = "websocket"


class MCPServerStatus(StrEnum):
    """Connection state of a registered MCP server.

    ``CONNECTED`` and ``CHANGED`` are resolvable — their active tools inject
    into agent context (a ``CHANGED`` server has at least one rug-pulled tool
    withheld, but its other tools resolve normally). ``DISCONNECTED`` (never
    discovered) and ``UNREACHABLE`` (discovery failed) withhold all tools.
    """

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    UNREACHABLE = "unreachable"
    CHANGED = "changed"


class MCPServerConfig(BaseModel):
    """
    An MCP server registered at the OS level.
    Registered once via `arcana mcp add` — shared across all agents.
    Persisted to ~/.arcana/connections/mcps.json (no secrets here).
    """

    name: str  # human key: "notion-mcp", "gmail-mcp"
    server_url: str = ""  # remote endpoint for SSE; unused for stdio
    transport: MCPTransport = MCPTransport.SSE
    discovered_tools: list[ToolDefinition] = []  # populated on connect
    status: MCPServerStatus = MCPServerStatus.DISCONNECTED
    description: str = ""

    # stdio transport: the child is exec'd by argv (never a shell) with a
    # scoped environment — only ``env_allowlist`` names inherited from the
    # parent plus the explicit (non-secret) ``env`` entries.
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    env_allowlist: list[str] = []

    # SSE transport auth: a keyring reference (never the token itself). The
    # secret is resolved from the OS keyring at connect and injected as a
    # header; it is never persisted here, logged, or placed on a span.
    auth_key_ref: str | None = None

    # Per-server overrides for the adapter's env-backed defaults. ``None`` means
    # "use the ARCANA_MCP_* default".
    call_timeout_s: float | None = None
    max_result_bytes: int | None = None
    ephemeral_stdio: bool = False  # spawn a fresh stdio process per call

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.discovered_tools]

    def get_tool(self, tool_name: str) -> ToolDefinition | None:
        return next((t for t in self.discovered_tools if t.name == tool_name), None)


class ToolSubscription(BaseModel):
    """
    An agent's subscription to a tool (or a whole server) from the OS-level registry.
    Format: "server_name/tool_name" e.g. "notion-mcp/search_pages"
    OR a builtin: "builtin/web_search"
    OR a whole-server wildcard: "notion-mcp/*" (every active tool on that server)
    """

    qualified_name: str  # "notion-mcp/search_pages"

    @property
    def server_name(self) -> str | None:
        parts = self.qualified_name.split("/", 1)
        return parts[0] if len(parts) == 2 and parts[0] != "builtin" else None

    @property
    def tool_name(self) -> str:
        parts = self.qualified_name.split("/", 1)
        return parts[1] if len(parts) == 2 else parts[0]

    @property
    def is_builtin(self) -> bool:
        return self.qualified_name.startswith("builtin/")

    @property
    def is_wildcard(self) -> bool:
        """True for a ``server/*`` subscription — every active tool on that server.

        Expanded at resolution time so tools discovered *after* the subscription
        flow in automatically, while withheld (``CHANGED``) tools stay excluded.
        """
        return self.server_name is not None and self.tool_name == "*"

    def __str__(self) -> str:
        return self.qualified_name
