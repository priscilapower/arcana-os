"""Tests for the `arcana mcp` group — `add` plus list / show / refresh / approve / remove.

Discovery is exercised against a fake adapter injected into the registry, so no
network or real MCP server is needed — the real registry diff/persist logic
still runs.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.mcp as mcp_mod
from arcana.agents.registry import AgentRegistry
from arcana.tools.registry import MCPRegistry
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


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


def _fake_adapter(tools: list[ToolDefinition] | None = None, *, fail: bool = False):
    """A stand-in for MCPToolAdapter: discovers canned tools, never connects."""

    class _Fake:
        def __init__(self, cfg: MCPServerConfig) -> None:
            self._cfg = cfg

        async def discover(self) -> list[ToolDefinition]:
            if fail:
                raise ConnectionError("no route to host")
            return list(tools or [])

        async def aclose(self) -> None:
            return None

    return _Fake


def _tool(name: str, *, server: str = "notion-mcp", desc: str = "Search pages") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=desc,
        input_schema={"type": "object", "properties": {}},
        type=ToolType.MCP,
        mcp_server_name=server,
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a temp ARCANA_HOME and stub out keyring writes."""
    mcps = tmp_path / "connections" / "mcps.json"
    monkeypatch.setattr(mcp_mod, "MCPS_PATH", mcps)
    monkeypatch.setattr(mcp_mod, "AGENTS_BASE", tmp_path / "agents")
    return tmp_path


def _seed_server(home: Path, server: MCPServerConfig) -> None:
    reg = MCPRegistry(connections_file=home / "connections" / "mcps.json")
    reg.load()
    reg.register_server(server)


# ---------------------------------------------------------------------------
# mcp add
# ---------------------------------------------------------------------------


def test_mcp_add_sse_discovers_and_persists(home, monkeypatch):
    monkeypatch.setattr("arcana.tools.registry.MCPToolAdapter", _fake_adapter([_tool("search_pages")]))
    result = runner.invoke(app, ["mcp", "add", "--name", "notion-mcp", "--url", "https://mcp.notion.com/sse"])
    assert result.exit_code == 0, result.output
    assert "search_pages" in result.output

    data = json.loads((home / "connections" / "mcps.json").read_text())
    server = data["servers"][0]
    assert server["name"] == "notion-mcp"
    assert server["transport"] == "sse"
    assert server["status"] == "connected"
    assert [t["name"] for t in server["discovered_tools"]] == ["search_pages"]


def test_mcp_add_stdio_inferred_from_command(home, monkeypatch):
    monkeypatch.setattr("arcana.tools.registry.MCPToolAdapter", _fake_adapter([_tool("do", server="local-mcp")]))
    result = runner.invoke(app, ["mcp", "add", "--name", "local-mcp", "--command", "my-server", "--arg", "--stdio"])
    assert result.exit_code == 0, result.output
    data = json.loads((home / "connections" / "mcps.json").read_text())
    server = data["servers"][0]
    assert server["transport"] == "stdio"
    assert server["command"] == "my-server"
    assert server["args"] == ["--stdio"]


def test_mcp_add_requires_url_or_command(home):
    result = runner.invoke(app, ["mcp", "add", "--name", "x"])
    assert result.exit_code == 1
    assert "Provide --url" in result.output


def test_mcp_add_rejects_both_url_and_command(home):
    result = runner.invoke(app, ["mcp", "add", "--name", "x", "--url", "https://a/sse", "--command", "c"])
    assert result.exit_code == 1


def test_mcp_add_rejects_reserved_builtin_name(home):
    result = runner.invoke(app, ["mcp", "add", "--name", "builtin", "--url", "https://a/sse"])
    assert result.exit_code == 1
    assert "reserved" in result.output


def test_mcp_add_rejects_name_with_slash(home):
    result = runner.invoke(app, ["mcp", "add", "--name", "foo/bar", "--url", "https://a/sse"])
    assert result.exit_code == 1
    assert "Invalid server name" in result.output


def test_mcp_add_keyring_failure_exits_cleanly(home, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("no keyring backend")

    monkeypatch.setattr("keyring.set_password", _boom)
    result = runner.invoke(
        app,
        ["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse", "--header", "Authorization=Bearer t"],
    )
    assert result.exit_code == 1
    assert "keyring" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    # Nothing persisted — the failure happens before discover/save.
    assert not (home / "connections" / "mcps.json").exists()


def test_mcp_add_header_stored_in_keyring_never_echoed(home, monkeypatch):
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr("keyring.set_password", lambda svc, ref, val: store.__setitem__((svc, ref), val))
    monkeypatch.setattr("arcana.tools.registry.MCPToolAdapter", _fake_adapter([_tool("search_pages")]))

    secret = "sk-mcp-super-secret"
    result = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "--name",
            "notion-mcp",
            "--url",
            "https://mcp.notion.com/sse",
            "--header",
            f"Authorization=Bearer {secret}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert secret not in result.output
    # Token in keyring under a per-server ref; only the ref is persisted.
    assert store[("arcana", "mcp_notion-mcp_auth")] == secret
    data = json.loads((home / "connections" / "mcps.json").read_text())
    assert data["servers"][0]["auth_key_ref"] == "mcp_notion-mcp_auth"
    assert secret not in (home / "connections" / "mcps.json").read_text()


def test_mcp_add_duplicate_name_rejected(home, monkeypatch):
    _seed_server(
        home, MCPServerConfig(name="notion-mcp", server_url="https://a/sse", status=MCPServerStatus.CONNECTED)
    )
    result = runner.invoke(app, ["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_mcp_add_unreachable_persists_but_exits_nonzero(home, monkeypatch):
    monkeypatch.setattr("arcana.tools.registry.MCPToolAdapter", _fake_adapter(fail=True))
    result = runner.invoke(app, ["mcp", "add", "--name", "down-mcp", "--url", "https://down/sse"])
    assert result.exit_code == 1
    assert "could not be reached" in result.output
    data = json.loads((home / "connections" / "mcps.json").read_text())
    assert data["servers"][0]["status"] == "unreachable"


# ---------------------------------------------------------------------------
# mcp list / show
# ---------------------------------------------------------------------------


def test_mcp_list_json(home):
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CONNECTED,
            discovered_tools=[_tool("search_pages")],
        ),
    )
    result = runner.invoke(app, ["mcp", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["name"] == "notion-mcp"
    assert payload[0]["tools"] == 1


def test_mcp_show_redacts_auth(home):
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CONNECTED,
            auth_key_ref="mcp_notion-mcp_auth",
            discovered_tools=[_tool("search_pages")],
        ),
    )
    result = runner.invoke(app, ["mcp", "show", "notion-mcp"])
    assert result.exit_code == 0
    assert "mcp_notion-mcp_auth" in result.output  # the reference is fine to show
    assert "search_pages" in result.output


def test_mcp_show_unknown_exits_not_found(home):
    result = runner.invoke(app, ["mcp", "show", "ghost"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# mcp refresh + approve (the changed-tool gate)
# ---------------------------------------------------------------------------


def test_refresh_flags_changed_tool_and_approve_restores(home, monkeypatch):
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CONNECTED,
            discovered_tools=[_tool("search_pages", desc="Search pages")],
        ),
    )
    # A rug-pulled description on re-discovery → CHANGED, withheld.
    monkeypatch.setattr(
        "arcana.tools.registry.MCPToolAdapter",
        _fake_adapter([_tool("search_pages", desc="TOTALLY DIFFERENT now")]),
    )
    refreshed = runner.invoke(app, ["mcp", "refresh", "notion-mcp"])
    assert refreshed.exit_code == 0, refreshed.output
    assert "changed" in refreshed.output.lower()

    data = json.loads((home / "connections" / "mcps.json").read_text())
    assert data["servers"][0]["status"] == "changed"
    assert data["servers"][0]["discovered_tools"][0]["status"] == "changed"

    # The tool drops out of the subscribable inventory while changed.
    listed = runner.invoke(app, ["tools", "list", "--json"])
    assert "notion-mcp/search_pages" not in listed.output

    # Approve restores it.
    approved = runner.invoke(app, ["mcp", "approve", "notion-mcp", "--all"])
    assert approved.exit_code == 0, approved.output
    data = json.loads((home / "connections" / "mcps.json").read_text())
    assert data["servers"][0]["status"] == "connected"
    assert data["servers"][0]["discovered_tools"][0]["status"] == "active"


def test_approve_requires_tool_or_all(home):
    _seed_server(home, MCPServerConfig(name="notion-mcp", server_url="https://a/sse", status=MCPServerStatus.CHANGED))
    result = runner.invoke(app, ["mcp", "approve", "notion-mcp"])
    assert result.exit_code == 1


def test_approve_unknown_tool_exits_not_found(home):
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CHANGED,
            discovered_tools=[_tool("search_pages", desc="x").model_copy(update={"status": ToolStatus.CHANGED})],
        ),
    )
    result = runner.invoke(app, ["mcp", "approve", "notion-mcp", "--tool", "nope"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# mcp remove (dependent scan + keyring)
# ---------------------------------------------------------------------------


def _make_agent_subscribed(home: Path, qualified_name: str, *, name: str = "hermit") -> None:
    reg = AgentRegistry(home / "agents")
    record = reg.create(name=name, card=Card.HERMIT, model="")
    reg.save(record.model_copy(update={"tool_subscriptions": [qualified_name]}))


def test_remove_aborts_when_dependents_without_force(home):
    _seed_server(
        home, MCPServerConfig(name="notion-mcp", server_url="https://a/sse", status=MCPServerStatus.CONNECTED)
    )
    _make_agent_subscribed(home, "notion-mcp/search_pages")
    result = runner.invoke(app, ["mcp", "remove", "notion-mcp", "--yes"])
    assert result.exit_code == 1
    assert "hermit" in result.output
    # Still present.
    assert "notion-mcp" in (home / "connections" / "mcps.json").read_text()


def test_remove_json_dependents_preserves_same_named_agents(home):
    _seed_server(
        home, MCPServerConfig(name="notion-mcp", server_url="https://a/sse", status=MCPServerStatus.CONNECTED)
    )
    # Two distinct agents that happen to share a name both depend on the server.
    _make_agent_subscribed(home, "notion-mcp/search_pages", name="twin")
    _make_agent_subscribed(home, "notion-mcp/search_pages", name="twin")
    result = runner.invoke(app, ["mcp", "remove", "notion-mcp", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["aborted"] == "dependents"
    assert len(payload["dependents"]) == 2  # a dict keyed by name would collapse to 1


def test_remove_with_force_deletes_and_removes_owned_credential(home, monkeypatch):
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr("keyring.delete_password", lambda svc, ref: deleted.append((svc, ref)))
    _seed_server(
        home,
        MCPServerConfig(
            name="notion-mcp",
            server_url="https://a/sse",
            status=MCPServerStatus.CONNECTED,
            auth_key_ref="mcp_notion-mcp_auth",
        ),
    )
    _make_agent_subscribed(home, "notion-mcp/search_pages")
    result = runner.invoke(app, ["mcp", "remove", "notion-mcp", "--force", "--yes"])
    assert result.exit_code == 0, result.output
    data = json.loads((home / "connections" / "mcps.json").read_text())
    assert data["servers"] == []
    assert ("arcana", "mcp_notion-mcp_auth") in deleted


def test_remove_confirms_without_yes(home):
    _seed_server(
        home, MCPServerConfig(name="notion-mcp", server_url="https://a/sse", status=MCPServerStatus.CONNECTED)
    )
    result = runner.invoke(app, ["mcp", "remove", "notion-mcp"], input="n\n")
    assert result.exit_code != 0  # aborted
    assert "notion-mcp" in (home / "connections" / "mcps.json").read_text()
