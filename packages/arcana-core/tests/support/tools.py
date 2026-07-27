"""Shared test doubles for the tool gateway and builtin tools.

Defined once so the tool test modules stop each re-rolling a slightly different
copy:

* :class:`EchoAdapter` — a trivial in-memory ``ToolAdapter`` that echoes its
  argument back, for exercising gateway routing and the ``Agent`` loop without
  any I/O.
* :func:`make_mock_client` — an ``httpx.AsyncClient`` wired to a ``MockTransport``
  handler, with ``follow_redirects=False`` so the egress guard walks redirects
  itself (matching how ``BuiltinToolAdapter`` builds its real client).
* :data:`PUBLIC_IP` — a public literal host that never triggers DNS and always
  clears the SSRF check, so fetch tests exercise the guard without resolving names.
"""

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
from mcp.types import CallToolResult, ContentBlock, ListToolsResult, TextContent, Tool

from arcana.tools.adapters.base import ToolAdapter
from arcana.tools.adapters.mcp import SessionFactory
from arcana.tools.builtins.fs.config import FsToolsConfig
from arcana.tools.builtins.fs.handlers import FsTools
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import MCPServerConfig, ToolDefinition, ToolResult, ToolType

PUBLIC_IP = "93.184.216.34"


def seed_mcp_registry(*servers: MCPServerConfig) -> MCPRegistry:
    """An in-memory registry: builtins plus the given servers, no disk, no connect.

    The one place the registry's private slots are seeded for a test, so the many
    modules that need "builtins + one fake server, resolvable" stop each poking
    ``_register_builtins`` / ``_servers`` / ``_loaded`` by hand.
    """
    registry = MCPRegistry()
    registry._register_builtins()
    for server in servers:
        registry._servers[server.name] = server
    registry._loaded = True
    return registry


class EchoAdapter(ToolAdapter):
    """In-memory echo tool — a no-I/O fixture for the gateway and Agent loop."""

    type = ToolType.BUILTIN

    def provides(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="echo",
                description="echoes its message back",
                input_schema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                type=ToolType.BUILTIN,
            )
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(tool_name=name, success=True, output=args.get("message", ""))


class RecordingAdapter(ToolAdapter):
    """A builtin adapter that records every ``execute`` — to prove a *denied* call
    never runs.

    ``executed`` is the ordered list of tool names the gateway actually routed
    here; a permission or guardrail test asserts it stays empty when a call is
    refused, which is the "adapter never performs the action" half of a block.
    Provides a single tool, ``probe`` by default, so the offered/dispatched name
    is under the test's control.
    """

    type = ToolType.BUILTIN

    def __init__(self, tool_name: str = "probe") -> None:
        self.tool_name = tool_name
        self.executed: list[str] = []

    def provides(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self.tool_name,
                description="records that it ran",
                input_schema={"type": "object", "properties": {}},
                type=ToolType.BUILTIN,
            )
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        self.executed.append(name)
        return ToolResult(tool_name=name, success=True, output="ran")


def make_mock_client(handler: Any) -> httpx.AsyncClient:
    """An AsyncClient backed by ``handler`` via MockTransport; no real network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def fs_tools(root: Path, **overrides: Any) -> FsTools:
    """The filesystem builtins jailed to ``root`` (a ``tmp_path``), never $HOME.

    Defined once so every filesystem test builds the same default-closed jail;
    pass ``overrides`` for the config knob under test (``hard_delete``,
    ``max_write_bytes``, …).
    """
    return FsTools(FsToolsConfig(allowed_roots=[root], **overrides))


# ---------------------------------------------------------------------------
# MCP fakes — an in-memory ClientSession so the adapter tests never open a real
# transport or spawn a subprocess.
# ---------------------------------------------------------------------------


def connected_mcp_config(name: str = "notion-mcp", tool: str = "search_pages") -> MCPServerConfig:
    """A ``connected`` MCP server config exposing one discovered tool."""
    return MCPServerConfig(
        name=name,
        server_url="https://mcp.example.com/sse",
        status="connected",
        discovered_tools=[
            ToolDefinition(
                name=tool,
                description="Search pages",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
                type=ToolType.MCP,
                mcp_server_name=name,
            )
        ],
    )


def mcp_tool(name: str, *, description: str = "", schema: dict[str, Any] | None = None) -> Tool:
    """An MCP ``Tool`` as ``list_tools`` would return it."""
    return Tool(name=name, description=description, inputSchema=schema or {"type": "object", "properties": {}})


def tool_result(*blocks: ContentBlock, is_error: bool = False) -> CallToolResult:
    """A ``CallToolResult`` with the given content blocks."""
    return CallToolResult(content=list(blocks), isError=is_error)


def text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    """A single-text-block ``CallToolResult``."""
    return tool_result(TextContent(type="text", text=text), is_error=is_error)


class FakeMCPSession:
    """In-memory MCP session: records calls, returns canned results.

    Duck-types :class:`arcana.tools.adapters.mcp.MCPSession`. ``call_delay``
    holds the lock long enough for a concurrency test to observe serialisation.
    """

    def __init__(
        self,
        *,
        tools: list[Tool] | None = None,
        results: dict[str, CallToolResult] | None = None,
        raise_on_call: Exception | None = None,
        call_delay: float = 0.0,
    ) -> None:
        self._tools = tools or []
        self._results = results or {}
        self._raise_on_call = raise_on_call
        self._call_delay = call_delay
        self.initialized = 0
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.active_calls = 0
        self.max_concurrent_calls = 0

    async def initialize(self) -> Any:
        self.initialized += 1

    async def list_tools(self) -> ListToolsResult:
        return ListToolsResult(tools=self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        self.calls.append((name, arguments))
        self.active_calls += 1
        self.max_concurrent_calls = max(self.max_concurrent_calls, self.active_calls)
        try:
            if self._call_delay:
                await asyncio.sleep(self._call_delay)
            if self._raise_on_call is not None:
                raise self._raise_on_call
            return self._results.get(name, text_result("ok"))
        finally:
            self.active_calls -= 1


def session_factory(session: FakeMCPSession, *, connects: list[int] | None = None) -> SessionFactory:
    """A ``SessionFactory`` that yields ``session`` with a real (empty) stack.

    Initialises the session as the real ``_open`` does, so the injected path
    exercises the same contract. Pass ``connects`` to count (re)connects.
    """

    async def _factory() -> tuple[FakeMCPSession, AsyncExitStack]:
        if connects is not None:
            connects.append(1)
        await session.initialize()
        return session, AsyncExitStack()

    return _factory


def failing_session_factory(exc: Exception) -> SessionFactory:
    """A ``SessionFactory`` that always fails to connect."""

    async def _factory() -> tuple[FakeMCPSession, AsyncExitStack]:
        raise exc

    return _factory
