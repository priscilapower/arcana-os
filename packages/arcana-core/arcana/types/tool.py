"""Tool, Skill, MCP connection, and MCPRegistry types."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolType(StrEnum):
    BUILTIN = "builtin"
    MCP = "mcp"
    CUSTOM = "custom"


class ToolDefinition(BaseModel):
    """Schema for a callable tool. Injected into agent context at session start."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = {}
    type: ToolType = ToolType.BUILTIN
    mcp_server_name: str | None = None  # e.g. "notion-mcp"

    @property
    def qualified_name(self) -> str:
        """Fully qualified name: server_name/tool_name for MCP tools."""
        if self.mcp_server_name:
            return f"{self.mcp_server_name}/{self.name}"
        return self.name


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    output: Any = None
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


class MCPServerConfig(BaseModel):
    """
    An MCP server registered at the OS level.
    Registered once via `arcana connect mcp` — shared across all agents.
    Persisted to ~/.arcana/connections/mcps.json (no secrets here).
    """

    name: str  # human key: "notion-mcp", "gmail-mcp"
    server_url: str
    transport: MCPTransport = MCPTransport.SSE
    discovered_tools: list[ToolDefinition] = []  # populated on connect
    status: str = "disconnected"  # connected | error | discovering
    description: str = ""

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.discovered_tools]

    def get_tool(self, tool_name: str) -> ToolDefinition | None:
        return next((t for t in self.discovered_tools if t.name == tool_name), None)


class ToolSubscription(BaseModel):
    """
    An agent's subscription to a specific tool from the OS-level registry.
    Format: "server_name/tool_name" e.g. "notion-mcp/search_pages"
    OR a builtin: "builtin/web_search"
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

    def __str__(self) -> str:
        return self.qualified_name
