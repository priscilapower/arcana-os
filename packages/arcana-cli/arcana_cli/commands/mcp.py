"""arcana mcp — register and manage MCP server connections and their tools.

Every command is a thin wrapper over :class:`MCPRegistry`: parse, call one core
service, render. Secrets live only in the OS keyring — an ``mcps.json`` entry
keeps a reference, never a token, and no command echoes one.
"""

import re
from typing import Any

import keyring
import typer
from rich.console import Console
from rich.table import Table

from arcana.agents.registry import AgentRegistry
from arcana.tools.adapters.mcp import KEYRING_SERVICE
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import (
    MCPServerConfig,
    MCPServerStatus,
    MCPTransport,
    ToolStatus,
)
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_ERROR, EXIT_NOT_FOUND, emit_json, truncate
from arcana_cli.constants import AGENTS_BASE, MCPS_PATH
from arcana_cli.ui.theme import GREEN, ORANGE, RED, TXT3, dim, err, hl, make_table, ok, warn

app = typer.Typer(
    help="Manage MCP server connections and their tools (add / list / show / refresh / approve / remove)."
)
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_registry() -> MCPRegistry:
    reg = MCPRegistry(connections_file=MCPS_PATH)
    reg.load()
    return reg


def _resolve_server(reg: MCPRegistry, name: str) -> MCPServerConfig:
    """Return the named server, or exit ``2`` (not found)."""
    server = reg.get_server(name)
    if server is None:
        console.print(err(f"No MCP server named {name!r}."))
        console.print(dim("  Run: arcana mcp list"))
        raise typer.Exit(EXIT_NOT_FOUND)
    return server


def _auth_ref(name: str) -> str:
    """Keyring reference for a token this CLI stores on behalf of server ``name``."""
    return f"mcp_{name}_auth"


_STATUS_COLORS: dict[MCPServerStatus, str] = {
    MCPServerStatus.CONNECTED: GREEN,
    MCPServerStatus.CHANGED: ORANGE,
    MCPServerStatus.UNREACHABLE: RED,
    MCPServerStatus.DISCONNECTED: TXT3,
}


def _status_markup(status: MCPServerStatus) -> str:
    return f"[{_STATUS_COLORS.get(status, TXT3)}]{status.value}[/]"


def _tool_count(server: MCPServerConfig) -> str:
    """``active/total`` when some tools are withheld, else the plain total."""
    total = len(server.discovered_tools)
    changed = sum(1 for t in server.discovered_tools if t.status is ToolStatus.CHANGED)
    return f"{total - changed}/{total}" if changed else str(total)


def _stdio_repr(server: MCPServerConfig) -> str:
    if not server.command:
        return "(no command)"
    return " ".join([server.command, *server.args])


def _tools_table(server: MCPServerConfig) -> Table:
    table = make_table(f"{server.name} · tools")
    table.add_column("Tool", style="bold")
    table.add_column("Status")
    table.add_column("Description", style=TXT3)
    for t in server.discovered_tools:
        status = f"[{ORANGE}]changed[/]" if t.status is ToolStatus.CHANGED else f"[{GREEN}]active[/]"
        table.add_row(t.name, status, truncate(t.description))
    return table


def _print_server(server: MCPServerConfig) -> None:
    """Human detail for one server. Only the keyring reference is shown — the
    token itself never leaves the keychain."""
    endpoint = server.server_url if server.transport is MCPTransport.SSE else _stdio_repr(server)
    auth = f"keyring ref '{server.auth_key_ref}'" if server.auth_key_ref else "(none)"
    console.print(f"\n  {hl('Name:')}      {server.name}")
    console.print(f"  {hl('Transport:')} {server.transport.value}")
    console.print(f"  {hl('Endpoint:')}  {endpoint}")
    console.print(f"  {hl('Status:')}    {_status_markup(server.status)}")
    console.print(f"  {hl('Auth:')}      {auth}")
    if server.description:
        console.print(f"  {hl('About:')}     {server.description}")
    console.print()
    if server.discovered_tools:
        console.print(_tools_table(server))
    else:
        console.print(dim("  No tools discovered."))


def _server_summary(server: MCPServerConfig) -> dict[str, Any]:
    return {
        "name": server.name,
        "transport": server.transport.value,
        "status": server.status.value,
        "tools": len(server.discovered_tools),
        "changed_tools": sum(1 for t in server.discovered_tools if t.status is ToolStatus.CHANGED),
    }


def _server_detail(server: MCPServerConfig) -> dict[str, Any]:
    return {
        "name": server.name,
        "transport": server.transport.value,
        "server_url": server.server_url,
        "command": server.command,
        "args": list(server.args),
        "status": server.status.value,
        "description": server.description,
        "auth_key_ref": server.auth_key_ref,  # a reference, never the token
        "tools": [
            {
                "name": t.name,
                "qualified_name": t.qualified_name,
                "status": t.status.value,
                "description": t.description,
            }
            for t in server.discovered_tools
        ],
    }


def _dependent_agents(name: str) -> list[tuple[str, list[str]]]:
    """Agents subscribed to any ``<name>/*`` tool, with the subscriptions they lose."""
    prefix = f"{name}/"
    result: list[tuple[str, list[str]]] = []
    for agent in AgentRegistry(AGENTS_BASE).list():
        subs = [s for s in agent.tool_subscriptions if s.startswith(prefix)]
        if subs:
            result.append((agent.name, subs))
    return result


def _delete_owned_credential(server: MCPServerConfig) -> None:
    """Delete only a keyring token this CLI created (``mcp_<name>_auth``).

    A user-supplied ``--auth-key`` may be shared across servers, so it is left
    untouched; only the per-server reference we own is removed.
    """
    if server.auth_key_ref and server.auth_key_ref == _auth_ref(server.name):
        try:
            keyring.delete_password(KEYRING_SERVICE, server.auth_key_ref)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# arcana mcp add
# ---------------------------------------------------------------------------


_RESERVED_NAMES = frozenset({"builtin"})
# A server name becomes the ``{name}/{tool}`` qualified-name prefix, so it must
# not contain the ``/`` separator (or whitespace) and must not shadow the
# reserved ``builtin`` namespace — either would silently mis-route resolution.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_server_name(name: str) -> None:
    if name.lower() in _RESERVED_NAMES:
        console.print(err(f"{name!r} is reserved. Choose another server name."))
        raise typer.Exit(EXIT_ERROR)
    if not _NAME_PATTERN.match(name):
        console.print(err(f"Invalid server name {name!r}. Use letters, digits, '.', '_', or '-' (no '/' or spaces)."))
        raise typer.Exit(EXIT_ERROR)


def _infer_transport(url: str | None, command: str | None, transport: str | None) -> MCPTransport:
    if url and command:
        console.print(err("Pass either --url (SSE) or --command (stdio), not both."))
        raise typer.Exit(EXIT_ERROR)
    if not url and not command:
        console.print(err("Provide --url for an SSE server or --command for a stdio server."))
        raise typer.Exit(EXIT_ERROR)
    inferred = MCPTransport.SSE if url else MCPTransport.STDIO
    if transport is not None:
        try:
            requested = MCPTransport(transport.lower())
        except ValueError as exc:
            console.print(err(f"Unknown transport {transport!r}. Use 'sse' or 'stdio'."))
            raise typer.Exit(EXIT_ERROR) from exc
        if requested is not inferred:
            flag = "--url" if url else "--command"
            console.print(err(f"--transport {requested.value} conflicts with {flag} ({inferred.value})."))
            raise typer.Exit(EXIT_ERROR)
    return inferred


def _bearer_from_header(headers: list[str]) -> str:
    """Extract the bearer token from a single ``Authorization=...`` header.

    Only ``Authorization`` bearer headers are honoured — the SSE adapter sends
    no others. An empty value prompts hidden. The token is never echoed.
    """
    if len(headers) > 1:
        console.print(err("Only a single 'Authorization' header is supported for SSE MCP auth."))
        raise typer.Exit(EXIT_ERROR)
    raw = headers[0]
    if "=" not in raw:
        console.print(err("Invalid --header. Expected 'Authorization=Bearer <token>'."))
        raise typer.Exit(EXIT_ERROR)
    key, value = raw.split("=", 1)
    if key.strip().lower() != "authorization":
        console.print(err(f"Unsupported header {key.strip()!r}. SSE MCP auth accepts only 'Authorization'."))
        raise typer.Exit(EXIT_ERROR)
    token = value.strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()
    if not token:
        token = typer.prompt("Bearer token", hide_input=True).strip()
    if not token:
        console.print(err("No bearer token provided."))
        raise typer.Exit(EXIT_ERROR)
    return token


def _store_auth(name: str, headers: list[str], auth_key: str | None) -> str | None:
    """Resolve the server's ``auth_key_ref``, writing any inline token to keyring."""
    if auth_key and headers:
        console.print(err("Pass either --auth-key or --header, not both."))
        raise typer.Exit(EXIT_ERROR)
    if auth_key:
        return auth_key
    if not headers:
        return None
    token = _bearer_from_header(headers)
    ref = _auth_ref(name)
    try:
        keyring.set_password(KEYRING_SERVICE, ref, token)
    except Exception as exc:
        # No OS keyring backend (headless / CI / container). Fail cleanly before
        # anything is persisted — mcps.json is untouched at this point.
        console.print(err(f"Could not write the auth token to the OS keyring: {exc}"))
        raise typer.Exit(EXIT_ERROR) from exc
    return ref


@app.command("add")
def add_cmd(
    name: str = typer.Option(..., "--name", "-n", help="Server name, e.g. 'notion-mcp'"),
    url: str | None = typer.Option(None, "--url", help="SSE endpoint URL (implies --transport sse)"),
    command: str | None = typer.Option(None, "--command", help="stdio server command (implies --transport stdio)"),
    arg: list[str] | None = typer.Option(  # noqa: B008
        None, "--arg", help="stdio command argument (repeatable)"
    ),
    transport: str | None = typer.Option(None, "--transport", help="sse | stdio (inferred from --url / --command)"),
    header: list[str] | None = typer.Option(  # noqa: B008
        None, "--header", help="SSE auth 'Authorization=Bearer <token>' — stored in the keyring, never echoed"
    ),
    auth_key: str | None = typer.Option(
        None, "--auth-key", help="Existing keyring reference holding the bearer token (instead of --header)"
    ),
    description: str = typer.Option("", "--description", "-d", help="Human description"),
    json_: bool = typer.Option(False, "--json", help="Emit the result as JSON"),
) -> None:
    """Register an MCP server, discover its tools, and persist them.

    Transport is inferred: --url → SSE, --command → stdio. Auth material goes to
    the OS keyring; mcps.json stores only a reference. Discovery needs the server
    reachable; the discovered tools are printed on success.
    """
    _validate_server_name(name)
    resolved_transport = _infer_transport(url, command, transport)
    reg = _load_registry()
    if reg.get_server(name) is not None:
        console.print(err(f"An MCP server named {name!r} already exists."))
        console.print(dim(f"  Re-discover with: arcana mcp refresh {name}"))
        console.print(dim(f"  Or replace it:    arcana mcp remove {name}"))
        raise typer.Exit(EXIT_ERROR)

    auth_ref = _store_auth(name, header or [], auth_key)
    cfg = MCPServerConfig(
        name=name,
        transport=resolved_transport,
        server_url=url or "",
        command=command,
        args=list(arg or []),
        description=description,
        auth_key_ref=auth_ref,
    )
    server = run_async(reg.discover(cfg))

    if json_:
        emit_json(_server_detail(server))
    else:
        console.print(ok(f"Connected '{name}' — {len(server.discovered_tools)} tool(s) discovered."))
        _print_server(server)

    if server.status is MCPServerStatus.UNREACHABLE:
        console.print(warn(f"Server '{name}' was registered but could not be reached."))
        console.print(dim(f"  Retry with: arcana mcp refresh {name}"))
        raise typer.Exit(EXIT_ERROR)


# ---------------------------------------------------------------------------
# arcana mcp
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(json_: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    """List registered MCP servers."""
    reg = _load_registry()
    servers = reg.list_servers()
    if json_:
        emit_json([_server_summary(s) for s in servers])
        return
    if not servers:
        console.print(dim("No MCP servers connected. Run: arcana mcp add --name <n> --url <url>"))
        return
    table = make_table("MCP Servers")
    table.add_column("Name", style="bold")
    table.add_column("Transport")
    table.add_column("Tools")
    table.add_column("Status")
    for s in servers:
        table.add_row(s.name, s.transport.value, _tool_count(s), _status_markup(s.status))
    console.print(table)


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show a server's detail and discovered tools. Secrets are never printed."""
    reg = _load_registry()
    server = _resolve_server(reg, name)
    if json_:
        emit_json(_server_detail(server))
        return
    _print_server(server)


@app.command("refresh")
def refresh_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Re-discover a server's tools and re-run the changed-tool diff."""
    reg = _load_registry()
    server = _resolve_server(reg, name)
    before = {t.name: t.status for t in server.discovered_tools}
    refreshed = run_async(reg.discover(server))
    newly_changed = [
        t.name
        for t in refreshed.discovered_tools
        if t.status is ToolStatus.CHANGED and before.get(t.name) is not ToolStatus.CHANGED
    ]

    if json_:
        emit_json({**_server_detail(refreshed), "newly_changed": newly_changed})
    else:
        _print_server(refreshed)
        changed_total = sum(1 for t in refreshed.discovered_tools if t.status is ToolStatus.CHANGED)
        if changed_total:
            console.print(warn(f"\n  {changed_total} tool(s) changed and are withheld until approved."))
            console.print(dim(f"  Approve with: arcana mcp approve {name} --all"))

    if refreshed.status is MCPServerStatus.UNREACHABLE:
        console.print(warn(f"Server '{name}' is unreachable — showing last-known tools."))
        raise typer.Exit(EXIT_ERROR)


@app.command("approve")
def approve_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    tool: list[str] | None = typer.Option(  # noqa: B008
        None, "--tool", help="Qualified or bare tool name to approve (repeatable)"
    ),
    all_: bool = typer.Option(False, "--all", help="Approve every changed tool on the server"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Re-approve changed tools, admitting their new metadata (the trust gate)."""
    reg = _load_registry()
    server = _resolve_server(reg, name)

    if all_ and tool:
        console.print(err("Pass either --tool or --all, not both."))
        raise typer.Exit(EXIT_ERROR)
    if not all_ and not tool:
        console.print(err("Specify --tool <name> (repeatable) or --all."))
        raise typer.Exit(EXIT_ERROR)

    targets: list[str] | None
    if all_:
        targets = None
    else:
        targets = []
        for qn in tool or []:
            local = qn.split("/", 1)[1] if "/" in qn else qn
            td = server.get_tool(local)
            if td is None:
                console.print(err(f"No tool {local!r} on server {name!r}."))
                raise typer.Exit(EXIT_NOT_FOUND)
            if td.status is not ToolStatus.CHANGED:
                console.print(dim(f"  '{local}' is already active — skipping."))
                continue
            targets.append(local)
        if not targets:
            console.print(dim(f"Nothing to approve on '{name}'."))
            raise typer.Exit()

    approved = reg.approve(name, targets)
    refreshed = _resolve_server(reg, name)

    if json_:
        emit_json({"approved": approved, "status": refreshed.status.value})
        return
    if not approved:
        console.print(dim(f"Nothing to approve on '{name}' — no changed tools matched."))
        return
    console.print(ok(f"Approved {len(approved)} tool(s) on '{name}': {', '.join(approved)}"))
    console.print(f"  {hl('Status:')} {_status_markup(refreshed.status)}")


@app.command("remove")
def remove_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    force: bool = typer.Option(False, "--force", help="Remove even if agents subscribe to its tools"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Remove an MCP server, its discovered tools, and any keyring credential.

    Scans for agents subscribed to this server's tools and prints the blast
    radius; aborts unless --force is given.
    """
    reg = _load_registry()
    server = _resolve_server(reg, name)
    dependents = _dependent_agents(name)

    if dependents and not force:
        if json_:
            # A list (not a dict) so two agents sharing a name both survive.
            emit_json({"aborted": "dependents", "dependents": [{"agent": n, "tools": subs} for n, subs in dependents]})
        else:
            console.print(warn(f"Agents subscribe to tools from '{name}':"))
            for agent_name, subs in dependents:
                console.print(f"  {hl(agent_name)}  [{TXT3}]{', '.join(subs)}[/]")
            console.print(dim("\nRe-run with --force to remove anyway."))
        raise typer.Exit(EXIT_ERROR)

    if json_ and not yes:
        console.print(err("Use --yes with --json for a non-interactive remove."))
        raise typer.Exit(EXIT_ERROR)
    if not yes:
        typer.confirm(f"Remove MCP server '{name}'?", abort=True)

    if dependents and not json_:
        console.print(warn(f"{len(dependents)} agent(s) will lose these tools until re-subscribed elsewhere."))

    reg.remove_server(name)
    _delete_owned_credential(server)

    if json_:
        emit_json({"removed": name})
        return
    console.print(ok(f"MCP server '{name}' removed."))
