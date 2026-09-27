"""arcana tools — the subscribable tool inventory and per-agent subscriptions.

Thin wrappers over :class:`MCPRegistry` (the tool inventory) and
:class:`AgentRegistry` (the per-agent subscription list). ``list`` composes
builtins with each connected server's discovered tools; ``subscribe`` /
``unsubscribe`` make validated edits to ``Agent.tool_subscriptions``.

Each command body is a renderer-agnostic coroutine (``list_tools``,
``subscribe``, ``unsubscribe``) whose result is a
:class:`~arcana_cli.ui.renderer.Presentable` — a Rich view and the ``--json``
document of the same data — and whose errors go through
:func:`~arcana_cli.ui.renderer.fail`.
"""

from uuid import UUID

import typer

from arcana.agents.registry import AgentRegistry
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.agent import Agent as AgentRecord
from arcana.types.tool import BUILTIN_NAMESPACE, ToolDefinition, ToolStatus, ToolType
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_DENIED, EXIT_NOT_FOUND, truncate
from arcana_cli.command_impl import AGENT_METAVAR, command_impl
from arcana_cli.constants import AGENTS_BASE, CONNECTIONS_PATH, MCPS_PATH
from arcana_cli.ui.renderer import Renderer, View, confirm_or_cancel, fail, lines, renderer_for
from arcana_cli.ui.theme import GREEN, TXT3, dim, make_table, ok, warn

app = typer.Typer(help="Inspect and subscribe agents to tools.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_registry() -> MCPRegistry:
    reg = MCPRegistry(connections_file=MCPS_PATH)
    reg.load()
    return reg


def resolve_agent(r: Renderer, name_or_id: str) -> AgentRecord:
    """Resolve a name or UUID to an agent, or fail (``2`` not found / ``1`` ambiguous)."""
    reg = AgentRegistry(AGENTS_BASE)
    try:
        uid = UUID(name_or_id)
    except ValueError:
        matches = [a for a in reg.list() if a.name == name_or_id]
        if not matches:
            fail(r, f"No agent named {name_or_id!r}.", code=EXIT_NOT_FOUND)
        if len(matches) > 1:
            fail(r, f"Ambiguous name {name_or_id!r}. Use one of these IDs:", *(f"  {a.id}" for a in matches))
        return matches[0]
    record = reg.get(uid)
    if record is None or record.is_archived:
        fail(r, f"No agent with ID {name_or_id!r}.", code=EXIT_NOT_FOUND)
    return record


def _subscription_name(tool: ToolDefinition) -> str:
    """Subscription-form name: ``builtin/<n>`` for builtins, ``server/<n>`` for MCP."""
    return tool.qualified_name if tool.mcp_server_name else f"{BUILTIN_NAMESPACE}/{tool.name}"


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
    if "/" not in qualified_name or qualified_name.startswith(f"{BUILTIN_NAMESPACE}/"):
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


async def _warn_if_toolless(r: Renderer, record: AgentRecord) -> None:
    """Best-effort warning (a note) when the agent's model can't accept tool calls.

    The subscription still writes — the tools simply won't be injected until the
    agent points at a tool-capable model. Resolution failures (no model, unknown
    connection) are swallowed so the warning never blocks the edit.
    """
    if not record.model:
        return
    try:
        supports = await _supports_tools(record.model)
    except Exception:
        return
    if not supports:
        r.note(warn(f"Model '{record.model}' does not support tool calls — subscribed tools won't be injected."))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(
    agent: str | None = typer.Option(
        None, "--agent", "-a", metavar=AGENT_METAVAR, help="Mark which tools this agent is subscribed to"
    ),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List subscribable tools (builtins + discovered MCP tools)."""
    run_async(list_tools(renderer_for(json_), agent=agent))


@command_impl("tools list")
async def list_tools(r: Renderer, *, agent: str | None) -> None:
    """The subscribable inventory; with ``agent``, which of it that agent is subscribed to."""
    reg = _load_registry()
    inventory = _active_inventory(reg)
    subscribed: set[str] = set()
    if agent is not None:
        subscribed = set(resolve_agent(r, agent).tool_subscriptions)

    rows = [
        {
            "qualified_name": _subscription_name(t),
            "type": t.type.value,
            "server": t.mcp_server_name,
            "description": t.description,
            **({"subscribed": _is_subscribed(t, subscribed)} if agent is not None else {}),
        }
        for t in inventory
    ]
    if not inventory:
        r.emit(View(dim("No tools available. Connect an MCP server: arcana mcp add ..."), rows))
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
    r.emit(View(table, rows))


@app.command("subscribe")
def subscribe_cmd(
    agent: str = typer.Argument(..., metavar=AGENT_METAVAR, help="Agent name or UUID"),
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
    run_async(subscribe(renderer_for(json_), agent, qualified_name))


@command_impl("tools subscribe")
async def subscribe(r: Renderer, agent: str, qualified_name: str) -> None:
    """Add one validated subscription (a no-op when the agent already has it)."""
    record = resolve_agent(r, agent)
    reg = _load_registry()

    # Shorthand: a bare, known server name means "all of this server".
    if "/" not in qualified_name and reg.get_server(qualified_name) is not None:
        qualified_name = f"{qualified_name}/*"

    if qualified_name.endswith("/*"):
        covered = _validate_wildcard(r, reg, qualified_name)
    else:
        covered = None
        _validate_tool(r, reg, qualified_name)

    if qualified_name in record.tool_subscriptions:
        r.emit(
            View(
                dim(f"Agent '{record.name}' is already subscribed to '{qualified_name}'."),
                {"agent": record.name, "tool": qualified_name, "subscribed": True, "changed": False},
            )
        )
        return

    updated = record.model_copy(update={"tool_subscriptions": [*record.tool_subscriptions, qualified_name]})
    AgentRegistry(AGENTS_BASE).save(updated)
    await _warn_if_toolless(r, record)

    if covered is not None:
        human = [ok(f"Agent '{record.name}' subscribed to all of '{qualified_name}' ({covered} active tool(s)).")]
        if covered == 0:
            human.append(dim("  The server has no active tools yet — they'll resolve once connected/approved."))
    else:
        human = [ok(f"Agent '{record.name}' subscribed to '{qualified_name}'.")]
    r.emit(
        View(
            lines(*human),
            {
                "agent": updated.name,
                "tool": qualified_name,
                "subscribed": True,
                "changed": True,
                "covered_tools": covered,
                "tool_subscriptions": list(updated.tool_subscriptions),
            },
        )
    )


def _validate_tool(r: Renderer, reg: MCPRegistry, qualified_name: str) -> None:
    """Reject an unknown tool (exit 2) or a changed/withheld one (exit 3)."""
    valid = {_subscription_name(t) for t in _active_inventory(reg)}
    if qualified_name in valid:
        return
    if _is_changed_tool(reg, qualified_name):
        server_name = qualified_name.split("/", 1)[0]
        fail(
            r,
            f"Tool {qualified_name!r} is changed and awaiting re-approval.",
            dim(f"  Approve it: arcana mcp approve {server_name} --tool {qualified_name}"),
            code=EXIT_DENIED,
        )
    fail(r, f"Unknown tool {qualified_name!r}.", dim("  See available tools: arcana tools list"), code=EXIT_NOT_FOUND)


def _validate_wildcard(r: Renderer, reg: MCPRegistry, qualified_name: str) -> int:
    """Validate a ``server/*`` subscription; return the count of active tools it covers."""
    server_name = qualified_name[: -len("/*")]
    server = reg.get_server(server_name)
    if server is None:
        fail(r, f"Unknown MCP server {server_name!r}.", dim("  See servers: arcana mcp list"), code=EXIT_NOT_FOUND)
    return sum(1 for t in server.discovered_tools if t.status is ToolStatus.ACTIVE)


@app.command("unsubscribe")
def unsubscribe_cmd(
    agent: str = typer.Argument(..., metavar=AGENT_METAVAR, help="Agent name or UUID"),
    qualified_name: str = typer.Argument(..., help="Tool to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Unsubscribe an agent from a tool (or a whole-server ``<server>``/``<server>/*``)."""
    run_async(unsubscribe(renderer_for(json_), agent, qualified_name, yes=yes))


@command_impl("tools unsubscribe")
async def unsubscribe(r: Renderer, agent: str, qualified_name: str, *, yes: bool) -> None:
    """Drop one subscription once confirmed (or with ``yes``); under ``--json`` the question fails closed."""
    record = resolve_agent(r, agent)
    # Shorthand: a bare server name removes its whole-server subscription.
    if "/" not in qualified_name and f"{qualified_name}/*" in record.tool_subscriptions:
        qualified_name = f"{qualified_name}/*"
    if qualified_name not in record.tool_subscriptions:
        fail(r, f"Agent '{record.name}' is not subscribed to {qualified_name!r}.", code=EXIT_NOT_FOUND)

    if not yes:
        await confirm_or_cancel(r, f"Unsubscribe '{record.name}' from '{qualified_name}'?")

    remaining = [s for s in record.tool_subscriptions if s != qualified_name]
    updated = record.model_copy(update={"tool_subscriptions": remaining})
    AgentRegistry(AGENTS_BASE).save(updated)
    r.emit(
        View(
            ok(f"Agent '{record.name}' unsubscribed from '{qualified_name}'."),
            {"agent": updated.name, "tool": qualified_name, "subscribed": False, "tool_subscriptions": remaining},
        )
    )
