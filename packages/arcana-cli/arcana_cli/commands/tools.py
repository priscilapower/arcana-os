"""arcana tools — the subscribable tool inventory and per-agent subscriptions.

Thin wrappers over :class:`MCPRegistry` (the tool inventory) and
:class:`AgentRegistry` (the per-agent subscription list). ``list`` composes
builtins with each connected server's discovered tools; ``subscribe`` /
``unsubscribe`` make validated edits to ``Agent.tool_subscriptions``.
"""

from uuid import UUID

import typer
from rich.console import Console

from arcana.agents.registry import AgentRegistry
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.agent import Agent as AgentRecord
from arcana.types.tool import ToolDefinition, ToolStatus, ToolType
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_DENIED, EXIT_ERROR, EXIT_NOT_FOUND, emit_json, truncate
from arcana_cli.constants import AGENTS_BASE, CONNECTIONS_PATH, MCPS_PATH
from arcana_cli.ui.theme import GREEN, TXT3, dim, err, make_table, ok, warn

app = typer.Typer(help="Inspect and subscribe agents to tools (list / subscribe / unsubscribe).")
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_registry() -> MCPRegistry:
    reg = MCPRegistry(connections_file=MCPS_PATH)
    reg.load()
    return reg


def resolve_agent(name_or_id: str) -> AgentRecord:
    """Resolve a name or UUID to an agent, or exit (``2`` not found / ``1`` ambiguous)."""
    reg = AgentRegistry(AGENTS_BASE)
    try:
        uid = UUID(name_or_id)
    except ValueError:
        matches = [a for a in reg.list() if a.name == name_or_id]
        if not matches:
            console.print(err(f"No agent named {name_or_id!r}."))
            raise typer.Exit(EXIT_NOT_FOUND)  # noqa: B904 — name miss, no cause to chain
        if len(matches) > 1:
            console.print(err(f"Ambiguous name {name_or_id!r}. Use one of these IDs:"))
            for a in matches:
                console.print(f"  {a.id}")
            raise typer.Exit(EXIT_ERROR)  # noqa: B904 — ambiguity, no cause to chain
        return matches[0]
    record = reg.get(uid)
    if record is None or record.is_archived:
        console.print(err(f"No agent with ID {name_or_id!r}."))
        raise typer.Exit(EXIT_NOT_FOUND)
    return record


def _subscription_name(tool: ToolDefinition) -> str:
    """Subscription-form name: ``builtin/<n>`` for builtins, ``server/<n>`` for MCP."""
    return tool.qualified_name if tool.mcp_server_name else f"builtin/{tool.name}"


def _is_subscribed(tool: ToolDefinition, subs: set[str]) -> bool:
    """Whether ``subs`` covers ``tool`` — directly or via a ``server/*`` wildcard."""
    if _subscription_name(tool) in subs:
        return True
    return tool.mcp_server_name is not None and f"{tool.mcp_server_name}/*" in subs


def _active_inventory(reg: MCPRegistry) -> list[ToolDefinition]:
    """Subscribable tools: builtins + active MCP tools (changed ones are withheld)."""
    return [t for t in reg.list_all_tools() if t.status is ToolStatus.ACTIVE]


def _is_changed_tool(reg: MCPRegistry, qualified_name: str) -> bool:
    """True if ``qualified_name`` names a discovered MCP tool currently withheld."""
    if "/" not in qualified_name or qualified_name.startswith("builtin/"):
        return False
    server_name, local = qualified_name.split("/", 1)
    server = reg.get_server(server_name)
    if server is None:
        return False
    tool = server.get_tool(local)
    return tool is not None and tool.status is ToolStatus.CHANGED


async def _supports_tools(model: str) -> bool:
    async with ModelGateway(connections=ConnectionStore(CONNECTIONS_PATH)) as gw:
        return await gw.supports_tools(model)


def _warn_if_toolless(record: AgentRecord) -> None:
    """Best-effort warning when the agent's model can't accept tool calls.

    The subscription still writes — the tools simply won't be injected until the
    agent points at a tool-capable model. Resolution failures (no model, unknown
    connection) are swallowed so the warning never blocks the edit.
    """
    if not record.model:
        return
    try:
        supports = run_async(_supports_tools(record.model))
    except Exception:
        return
    if not supports:
        console.print(
            warn(f"Model '{record.model}' does not support tool calls — subscribed tools won't be injected.")
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Mark which tools this agent is subscribed to"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List subscribable tools (builtins + discovered MCP tools)."""
    reg = _load_registry()
    inventory = _active_inventory(reg)
    subscribed: set[str] = set()
    if agent is not None:
        subscribed = set(resolve_agent(agent).tool_subscriptions)

    if json_:
        emit_json(
            [
                {
                    "qualified_name": _subscription_name(t),
                    "type": t.type.value,
                    "server": t.mcp_server_name,
                    "description": t.description,
                    **({"subscribed": _is_subscribed(t, subscribed)} if agent is not None else {}),
                }
                for t in inventory
            ]
        )
        return

    if not inventory:
        console.print(dim("No tools available. Connect an MCP server: arcana mcp add ..."))
        return

    table = make_table("Tools")
    table.add_column("Tool", style="bold")
    table.add_column("Type")
    table.add_column("Description", style=TXT3)
    if agent is not None:
        table.add_column("Subscribed")
    for t in sorted(inventory, key=lambda d: (d.type is not ToolType.BUILTIN, _subscription_name(d))):
        row = [_subscription_name(t), t.type.value, truncate(t.description)]
        if agent is not None:
            row.append(f"[{GREEN}]✓[/]" if _is_subscribed(t, subscribed) else dim("—"))
        table.add_row(*row)
    console.print(table)


@app.command("subscribe")
def subscribe_cmd(
    agent: str = typer.Argument(..., help="Agent name or UUID"),
    qualified_name: str = typer.Argument(
        ...,
        help="A tool ('notion-mcp/search_pages', 'builtin/web_search'), a whole server "
        "('notion-mcp' or 'notion-mcp/*'), etc.",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Subscribe an agent to a tool, or to a whole MCP server via ``<server>``/``<server>/*``.

    A whole-server subscription is stored as ``<server>/*`` and expands to every
    active tool at session start, so tools discovered later flow in automatically.
    """
    record = resolve_agent(agent)
    reg = _load_registry()

    # Shorthand: a bare, known server name means "all of this server".
    if "/" not in qualified_name and reg.get_server(qualified_name) is not None:
        qualified_name = f"{qualified_name}/*"

    if qualified_name.endswith("/*"):
        covered = _validate_wildcard(reg, qualified_name)
    else:
        covered = None
        _validate_tool(reg, qualified_name)

    if qualified_name in record.tool_subscriptions:
        if json_:
            emit_json({"agent": record.name, "tool": qualified_name, "subscribed": True, "changed": False})
        else:
            console.print(dim(f"Agent '{record.name}' is already subscribed to '{qualified_name}'."))
        return

    updated = record.model_copy(update={"tool_subscriptions": [*record.tool_subscriptions, qualified_name]})
    AgentRegistry(AGENTS_BASE).save(updated)
    _warn_if_toolless(record)

    if json_:
        emit_json(
            {
                "agent": updated.name,
                "tool": qualified_name,
                "subscribed": True,
                "changed": True,
                "covered_tools": covered,
                "tool_subscriptions": list(updated.tool_subscriptions),
            }
        )
        return
    if covered is not None:
        console.print(ok(f"Agent '{record.name}' subscribed to all of '{qualified_name}' ({covered} active tool(s))."))
        if covered == 0:
            console.print(dim("  The server has no active tools yet — they'll resolve once connected/approved."))
    else:
        console.print(ok(f"Agent '{record.name}' subscribed to '{qualified_name}'."))


def _validate_tool(reg: MCPRegistry, qualified_name: str) -> None:
    """Reject an unknown tool (exit 2) or a changed/withheld one (exit 3)."""
    valid = {_subscription_name(t) for t in _active_inventory(reg)}
    if qualified_name in valid:
        return
    if _is_changed_tool(reg, qualified_name):
        server_name = qualified_name.split("/", 1)[0]
        console.print(err(f"Tool {qualified_name!r} is changed and awaiting re-approval."))
        console.print(dim(f"  Approve it: arcana mcp approve {server_name} --tool {qualified_name}"))
        raise typer.Exit(EXIT_DENIED)
    console.print(err(f"Unknown tool {qualified_name!r}."))
    console.print(dim("  See available tools: arcana tools list"))
    raise typer.Exit(EXIT_NOT_FOUND)


def _validate_wildcard(reg: MCPRegistry, qualified_name: str) -> int:
    """Validate a ``server/*`` subscription; return the count of active tools it covers."""
    server_name = qualified_name[: -len("/*")]
    server = reg.get_server(server_name)
    if server is None:
        console.print(err(f"Unknown MCP server {server_name!r}."))
        console.print(dim("  See servers: arcana mcp list"))
        raise typer.Exit(EXIT_NOT_FOUND)
    return sum(1 for t in server.discovered_tools if t.status is ToolStatus.ACTIVE)


@app.command("unsubscribe")
def unsubscribe_cmd(
    agent: str = typer.Argument(..., help="Agent name or UUID"),
    qualified_name: str = typer.Argument(..., help="Tool to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Unsubscribe an agent from a tool (or a whole-server ``<server>``/``<server>/*``)."""
    record = resolve_agent(agent)
    # Shorthand: a bare server name removes its whole-server subscription.
    if "/" not in qualified_name and f"{qualified_name}/*" in record.tool_subscriptions:
        qualified_name = f"{qualified_name}/*"
    if qualified_name not in record.tool_subscriptions:
        console.print(err(f"Agent '{record.name}' is not subscribed to {qualified_name!r}."))
        raise typer.Exit(EXIT_NOT_FOUND)

    if json_ and not yes:
        console.print(err("Use --yes with --json for a non-interactive unsubscribe."))
        raise typer.Exit(EXIT_ERROR)
    if not yes:
        typer.confirm(f"Unsubscribe '{record.name}' from '{qualified_name}'?", abort=True)

    remaining = [s for s in record.tool_subscriptions if s != qualified_name]
    updated = record.model_copy(update={"tool_subscriptions": remaining})
    AgentRegistry(AGENTS_BASE).save(updated)

    if json_:
        emit_json(
            {"agent": updated.name, "tool": qualified_name, "subscribed": False, "tool_subscriptions": remaining}
        )
        return
    console.print(ok(f"Agent '{record.name}' unsubscribed from '{qualified_name}'."))
