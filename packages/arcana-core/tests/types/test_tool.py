from arcana.types import (
    MCPServerConfig,
    MCPTransport,
    Skill,
    ToolDefinition,
    ToolResult,
    ToolSubscription,
    ToolType,
)

# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------


def test_tool_definition_qualified_name_builtin():
    tool = ToolDefinition(name="web_search", description="Search the web", input_schema={})
    assert tool.qualified_name == "web_search"


def test_tool_definition_qualified_name_mcp():
    tool = ToolDefinition(
        name="search_pages",
        description="Search Notion pages",
        input_schema={},
        mcp_server_name="notion-mcp",
    )
    assert tool.qualified_name == "notion-mcp/search_pages"


def test_tool_definition_defaults():
    tool = ToolDefinition(name="my_tool", description="A tool", input_schema={})
    assert tool.type == ToolType.BUILTIN
    assert tool.mcp_server_name is None
    assert tool.output_schema == {}


def test_tool_type_values():
    assert ToolType.BUILTIN == "builtin"
    assert ToolType.MCP == "mcp"
    assert ToolType.CUSTOM == "custom"


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


def test_tool_result_defaults():
    result = ToolResult(tool_name="web_search", success=True)
    assert result.output is None
    assert result.error is None
    assert result.duration_ms == 0


def test_tool_result_success():
    result = ToolResult(tool_name="web_search", success=True, output={"hits": 5}, duration_ms=200)
    assert result.success is True
    assert result.output == {"hits": 5}


def test_tool_result_failure():
    result = ToolResult(tool_name="broken", success=False, error="Timeout")
    assert result.success is False
    assert result.error == "Timeout"


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


def test_skill_defaults():
    skill = Skill(id="research", name="Research", description="Deep research skill")
    assert skill.required_tool_names == []
    assert skill.prompt_strategy == ""
    assert skill.affinity_cards == []
    assert skill.tarot_name is None


def test_skill_with_tools():
    skill = Skill(
        id="research",
        name="Research",
        description="Deep research skill",
        required_tool_names=["notion-mcp/search_pages", "builtin/web_search"],
        affinity_cards=["the-hermit"],
    )
    assert len(skill.required_tool_names) == 2
    assert "the-hermit" in skill.affinity_cards


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------


def test_mcp_server_defaults():
    server = MCPServerConfig(name="notion-mcp", server_url="http://localhost:3000")
    assert server.transport == MCPTransport.SSE
    assert server.discovered_tools == []
    assert server.status == "disconnected"
    assert server.description == ""


def test_mcp_server_tool_names_empty():
    server = MCPServerConfig(name="notion-mcp", server_url="http://localhost:3000")
    assert server.tool_names == []


def test_mcp_server_tool_names_with_tools():
    tools = [
        ToolDefinition(name="search_pages", description="Search", input_schema={}),
        ToolDefinition(name="create_page", description="Create", input_schema={}),
    ]
    server = MCPServerConfig(name="notion-mcp", server_url="http://localhost:3000", discovered_tools=tools)
    assert server.tool_names == ["search_pages", "create_page"]


def test_mcp_server_get_tool_found():
    tool = ToolDefinition(name="search_pages", description="Search", input_schema={})
    server = MCPServerConfig(name="notion-mcp", server_url="http://localhost:3000", discovered_tools=[tool])
    found = server.get_tool("search_pages")
    assert found is not None
    assert found.name == "search_pages"


def test_mcp_server_get_tool_not_found():
    server = MCPServerConfig(name="notion-mcp", server_url="http://localhost:3000")
    assert server.get_tool("nonexistent") is None


def test_mcp_transport_values():
    assert MCPTransport.SSE == "sse"
    assert MCPTransport.STDIO == "stdio"
    assert MCPTransport.WEBSOCKET == "websocket"


# ---------------------------------------------------------------------------
# ToolSubscription
# ---------------------------------------------------------------------------


def test_tool_subscription_mcp_server_name():
    sub = ToolSubscription(qualified_name="notion-mcp/search_pages")
    assert sub.server_name == "notion-mcp"


def test_tool_subscription_mcp_tool_name():
    sub = ToolSubscription(qualified_name="notion-mcp/search_pages")
    assert sub.tool_name == "search_pages"


def test_tool_subscription_mcp_not_builtin():
    sub = ToolSubscription(qualified_name="notion-mcp/search_pages")
    assert sub.is_builtin is False


def test_tool_subscription_builtin_server_name_is_none():
    sub = ToolSubscription(qualified_name="builtin/web_search")
    assert sub.server_name is None


def test_tool_subscription_builtin_tool_name():
    sub = ToolSubscription(qualified_name="builtin/web_search")
    assert sub.tool_name == "web_search"


def test_tool_subscription_builtin_is_builtin():
    sub = ToolSubscription(qualified_name="builtin/web_search")
    assert sub.is_builtin is True


def test_tool_subscription_str():
    sub = ToolSubscription(qualified_name="notion-mcp/search_pages")
    assert str(sub) == "notion-mcp/search_pages"
