"""Tests for the `arcana tools` group: list / subscribe / unsubscribe."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.tools as tools_mod
from arcana.agents.registry import AgentRegistry
from arcana.tools.registry import MCPRegistry
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana.types.tool import (
    MCPServerConfig,
    MCPServerStatus,
    ToolDefinition,
    ToolStatus,
    ToolType,
)
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tools_mod, "MCPS_PATH", tmp_path / "connections" / "mcps.json")
    monkeypatch.setattr(tools_mod, "AGENTS_BASE", tmp_path / "agents")
    monkeypatch.setattr(tools_mod, "CONNECTIONS_PATH", tmp_path / "connections" / "models.json")
    return tmp_path


def _agent(home: Path, *, name: str = "hermit", subs: list[str] | None = None) -> AgentRecord:
    reg = AgentRegistry(home / "agents")
    record = reg.create(name=name, card=Card.HERMIT, model="")
    record = record.model_copy(update={"tool_subscriptions": subs or []})
    reg.save(record)
    return record


def _mcp_tool(name: str, *, server: str = "notion-mcp", status: ToolStatus = ToolStatus.ACTIVE) -> ToolDefinition:
    return ToolDefinition(
        name=name, description="Search", input_schema={}, type=ToolType.MCP, mcp_server_name=server, status=status
    )


def _seed_server(home: Path, server: MCPServerConfig) -> None:
    reg = MCPRegistry(connections_file=home / "connections" / "mcps.json")
    reg.load()
    reg.register_server(server)


def _reload(home: Path, name: str = "hermit") -> AgentRecord:
    match = [a for a in AgentRegistry(home / "agents").list() if a.name == name]
    assert match, "agent not found"
    return match[0]


# ---------------------------------------------------------------------------
# tools list
# ---------------------------------------------------------------------------


def test_tools_list_shows_builtins(home):
    result = runner.invoke(app, ["tools", "list", "--json"])
    assert result.exit_code == 0
    names = {t["qualified_name"] for t in json.loads(result.output)}
    assert "builtin/web_search" in names


def test_tools_list_agent_subscribed_column(home):
    _agent(home, subs=["builtin/web_search"])
    result = runner.invoke(app, ["tools", "list", "--agent", "hermit", "--json"])
    assert result.exit_code == 0
    by_name = {t["qualified_name"]: t for t in json.loads(result.output)}
    assert by_name["builtin/web_search"]["subscribed"] is True
    assert by_name["builtin/read_file"]["subscribed"] is False


def test_tools_list_hides_changed_mcp_tool(home):
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CHANGED,
            discovered_tools=[_mcp_tool("search_pages", status=ToolStatus.CHANGED)],
        ),
    )
    result = runner.invoke(app, ["tools", "list", "--json"])
    names = {t["qualified_name"] for t in json.loads(result.output)}
    assert "notion-mcp/search_pages" not in names


# ---------------------------------------------------------------------------
# tools subscribe
# ---------------------------------------------------------------------------


def test_subscribe_builtin_round_trips(home):
    _agent(home)
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "builtin/web_search"])
    assert result.exit_code == 0, result.output
    assert _reload(home).tool_subscriptions == ["builtin/web_search"]


def test_subscribe_mcp_tool(home):
    _agent(home)
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CONNECTED,
            discovered_tools=[_mcp_tool("search_pages")],
        ),
    )
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "notion-mcp/search_pages"])
    assert result.exit_code == 0, result.output
    assert _reload(home).tool_subscriptions == ["notion-mcp/search_pages"]


def test_subscribe_unknown_tool_exits_not_found(home):
    _agent(home)
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "notion-mcp/ghost"])
    assert result.exit_code == 2
    assert _reload(home).tool_subscriptions == []


def test_subscribe_changed_tool_exits_denied(home):
    _agent(home)
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CHANGED,
            discovered_tools=[_mcp_tool("search_pages", status=ToolStatus.CHANGED)],
        ),
    )
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "notion-mcp/search_pages"])
    assert result.exit_code == 3
    assert "approve" in result.output.lower()
    assert _reload(home).tool_subscriptions == []


def test_subscribe_duplicate_is_noop(home):
    _agent(home, subs=["builtin/web_search"])
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "builtin/web_search"])
    assert result.exit_code == 0
    assert _reload(home).tool_subscriptions == ["builtin/web_search"]


def test_subscribe_unknown_agent_exits_not_found(home):
    result = runner.invoke(app, ["tools", "subscribe", "ghost", "builtin/web_search"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# tools subscribe — whole-server wildcard
# ---------------------------------------------------------------------------


def _seed_two_tool_server(home: Path, *, name: str = "notion-mcp") -> None:
    _seed_server(
        home,
        MCPServerConfig(
            name=name,
            server_url="https://a/sse",
            status=MCPServerStatus.CONNECTED,
            discovered_tools=[_mcp_tool("search_pages", server=name), _mcp_tool("create_page", server=name)],
        ),
    )


def test_subscribe_whole_server_shorthand_stores_wildcard(home):
    _agent(home)
    _seed_two_tool_server(home)
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "notion-mcp"])
    assert result.exit_code == 0, result.output
    assert _reload(home).tool_subscriptions == ["notion-mcp/*"]
    assert "2 active tool" in result.output


def test_subscribe_explicit_wildcard(home):
    _agent(home)
    _seed_two_tool_server(home)
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "notion-mcp/*"])
    assert result.exit_code == 0, result.output
    assert _reload(home).tool_subscriptions == ["notion-mcp/*"]


def test_subscribe_wildcard_unknown_server_exits_not_found(home):
    _agent(home)
    result = runner.invoke(app, ["tools", "subscribe", "hermit", "ghost-mcp/*"])
    assert result.exit_code == 2
    assert _reload(home).tool_subscriptions == []


def test_wildcard_marks_individual_tools_subscribed_in_list(home):
    _agent(home, subs=["notion-mcp/*"])
    _seed_two_tool_server(home)
    result = runner.invoke(app, ["tools", "list", "--agent", "hermit", "--json"])
    by_name = {t["qualified_name"]: t for t in json.loads(result.output)}
    assert by_name["notion-mcp/search_pages"]["subscribed"] is True
    assert by_name["notion-mcp/create_page"]["subscribed"] is True
    assert by_name["builtin/web_search"]["subscribed"] is False


def test_unsubscribe_whole_server_shorthand(home):
    _agent(home, subs=["notion-mcp/*", "builtin/web_search"])
    result = runner.invoke(app, ["tools", "unsubscribe", "hermit", "notion-mcp", "--yes"])
    assert result.exit_code == 0, result.output
    assert _reload(home).tool_subscriptions == ["builtin/web_search"]


# ---------------------------------------------------------------------------
# tools unsubscribe
# ---------------------------------------------------------------------------


def test_unsubscribe_with_yes(home):
    _agent(home, subs=["builtin/web_search", "builtin/read_file"])
    result = runner.invoke(app, ["tools", "unsubscribe", "hermit", "builtin/web_search", "--yes"])
    assert result.exit_code == 0, result.output
    assert _reload(home).tool_subscriptions == ["builtin/read_file"]


def test_unsubscribe_confirms_without_yes(home):
    _agent(home, subs=["builtin/web_search"])
    result = runner.invoke(app, ["tools", "unsubscribe", "hermit", "builtin/web_search"], input="n\n")
    assert result.exit_code != 0  # aborted
    assert _reload(home).tool_subscriptions == ["builtin/web_search"]


def test_unsubscribe_not_subscribed_exits_not_found(home):
    _agent(home)
    result = runner.invoke(app, ["tools", "unsubscribe", "hermit", "builtin/web_search", "--yes"])
    assert result.exit_code == 2


def test_unsubscribe_json_requires_yes(home):
    _agent(home, subs=["builtin/web_search"])
    result = runner.invoke(app, ["tools", "unsubscribe", "hermit", "builtin/web_search", "--json"])
    assert result.exit_code == 1
