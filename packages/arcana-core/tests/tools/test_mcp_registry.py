"""Integration tests for MCPRegistry — currently has zero test coverage."""

import json
from pathlib import Path

from arcana.tools.registry import MCPRegistry
from arcana.types.tool import MCPServerConfig, ToolDefinition, ToolSubscription, ToolType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path) -> MCPRegistry:
    """Return a fresh MCPRegistry pointing at a temp connections file."""
    reg = MCPRegistry()
    reg.CONNECTIONS_FILE = tmp_path / "mcps.json"  # type: ignore[assignment]
    return reg


def _connected_server(name: str = "test-mcp", tool_name: str = "do_thing") -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        server_url="http://localhost:9000/mcp",
        status="connected",
        discovered_tools=[
            ToolDefinition(
                name=tool_name,
                description="Does a thing",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                type=ToolType.MCP,
                mcp_server_name=name,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------


def test_builtins_exactly_five_tools(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    tools = [t for t in reg.list_all_tools() if t.type == ToolType.BUILTIN]
    assert len(tools) == 5


def test_builtin_names_are_correct(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    builtin_names = {t.name for t in reg.list_all_tools() if t.type == ToolType.BUILTIN}
    assert builtin_names == {"web_search", "fetch_url", "read_file", "write_file", "run_code"}


# ---------------------------------------------------------------------------
# resolve() — supports_tools=False
# ---------------------------------------------------------------------------


def test_resolve_returns_empty_when_tools_not_supported(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    sub = ToolSubscription(qualified_name="builtin/web_search")
    result = reg.resolve([sub], supports_tools=False)
    assert result == []


# ---------------------------------------------------------------------------
# resolve() — builtin subscriptions
# ---------------------------------------------------------------------------


def test_resolve_builtin_subscription_returns_tool(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    sub = ToolSubscription(qualified_name="builtin/web_search")
    tools = reg.resolve([sub])
    assert len(tools) == 1
    assert tools[0].name == "web_search"


def test_resolve_multiple_builtin_subscriptions(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    subs = [
        ToolSubscription(qualified_name="builtin/web_search"),
        ToolSubscription(qualified_name="builtin/run_code"),
    ]
    tools = reg.resolve(subs)
    names = {t.name for t in tools}
    assert names == {"web_search", "run_code"}


def test_resolve_unknown_builtin_returns_nothing(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    sub = ToolSubscription(qualified_name="builtin/nonexistent_tool")
    tools = reg.resolve([sub])
    assert tools == []


# ---------------------------------------------------------------------------
# resolve() — MCP server subscriptions
# ---------------------------------------------------------------------------


def test_resolve_disconnected_server_returns_nothing(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    disconnected = MCPServerConfig(
        name="ghost-mcp",
        server_url="http://localhost:9001/mcp",
        status="disconnected",
        discovered_tools=[
            ToolDefinition(
                name="ghost_tool",
                description="Ghost",
                input_schema={},
                type=ToolType.MCP,
                mcp_server_name="ghost-mcp",
            )
        ],
    )
    reg._servers["ghost-mcp"] = disconnected
    sub = ToolSubscription(qualified_name="ghost-mcp/ghost_tool")
    tools = reg.resolve([sub])
    assert tools == []


def test_resolve_connected_server_returns_tool(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg._servers["test-mcp"] = _connected_server("test-mcp", "do_thing")
    sub = ToolSubscription(qualified_name="test-mcp/do_thing")
    tools = reg.resolve([sub])
    assert len(tools) == 1
    assert tools[0].name == "do_thing"


def test_resolve_mixed_builtin_and_mcp(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg._servers["test-mcp"] = _connected_server("test-mcp", "do_thing")
    subs = [
        ToolSubscription(qualified_name="builtin/fetch_url"),
        ToolSubscription(qualified_name="test-mcp/do_thing"),
    ]
    tools = reg.resolve(subs)
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"fetch_url", "do_thing"}


# ---------------------------------------------------------------------------
# list_all_tools()
# ---------------------------------------------------------------------------


def test_list_all_tools_includes_builtins_only_when_no_servers(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    tools = reg.list_all_tools()
    assert len(tools) == 5  # only builtins
    assert all(t.type == ToolType.BUILTIN for t in tools)


def test_list_all_tools_includes_connected_server_tools(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg._servers["test-mcp"] = _connected_server("test-mcp", "do_thing")
    tools = reg.list_all_tools()
    names = {t.name for t in tools}
    assert "do_thing" in names
    assert "web_search" in names  # builtins still present


def test_list_all_tools_excludes_disconnected_server_tools(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    disconnected = MCPServerConfig(
        name="offline-mcp",
        server_url="http://localhost:9002/mcp",
        status="disconnected",
        discovered_tools=[
            ToolDefinition(name="offline_tool", description="Offline", input_schema={}, type=ToolType.MCP)
        ],
    )
    reg._servers["offline-mcp"] = disconnected
    tools = reg.list_all_tools()
    assert not any(t.name == "offline_tool" for t in tools)


# ---------------------------------------------------------------------------
# register_server() + save() + load() round-trip
# ---------------------------------------------------------------------------


def test_register_server_persists_to_disk(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    server = _connected_server("notion-mcp", "search_pages")
    reg.register_server(server)

    assert reg.CONNECTIONS_FILE.exists()
    data = json.loads(reg.CONNECTIONS_FILE.read_text())
    names = [s["name"] for s in data["servers"]]
    assert "notion-mcp" in names


def test_load_restores_registered_servers(tmp_path):
    reg1 = _make_registry(tmp_path)
    reg1.load()
    reg1.register_server(_connected_server("notion-mcp", "search_pages"))

    reg2 = _make_registry(tmp_path)
    reg2.load()
    assert reg2.get_server("notion-mcp") is not None


def test_remove_server_removes_from_disk(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.register_server(_connected_server("notion-mcp", "search_pages"))
    reg.remove_server("notion-mcp")

    data = json.loads(reg.CONNECTIONS_FILE.read_text())
    names = [s["name"] for s in data["servers"]]
    assert "notion-mcp" not in names


def test_list_servers_returns_registered_servers(tmp_path):
    reg = _make_registry(tmp_path)
    reg.load()
    reg.register_server(_connected_server("srv-a", "tool_a"))
    reg.register_server(_connected_server("srv-b", "tool_b"))

    servers = reg.list_servers()
    names = {s.name for s in servers}
    assert {"srv-a", "srv-b"} <= names
