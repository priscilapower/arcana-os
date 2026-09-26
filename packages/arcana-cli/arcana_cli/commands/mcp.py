"""arcana mcp — register and manage MCP server connections and their tools.

Every command is a thin wrapper over :class:`MCPRegistry`: parse, call one core
service, render. Secrets live only in the OS keyring — an ``mcps.json`` entry
keeps a reference, never a token, and no command echoes one.

Every command body is a renderer-agnostic coroutine (``list_servers``,
``show_server``, ``refresh_server``, ``add_server``, ``login_server``,
``approve_server``, ``remove_server``): its result is a
:class:`~arcana_cli.ui.renderer.Presentable` (a Rich view and the ``--json``
document of the same data), its errors go through
:func:`~arcana_cli.ui.renderer.fail` and its questions (a bearer token, a
removal confirmation, an OAuth sign-in's instructions) through the
:class:`~arcana_cli.ui.renderer.Renderer` it is handed, so under ``--json`` a
question fails closed naming the flag that answers it instead of blocking on stdin.
"""

import re
from dataclasses import dataclass
from typing import Any

import keyring
import typer
from rich.console import RenderableType
from rich.table import Table

from arcana.agents.registry import AgentRegistry
from arcana.auth import load_token, save_token
from arcana.tools.adapters.mcp import KEYRING_SERVICE
from arcana.tools.registry import MCPRegistry
from arcana.types.auth import AuthType, OAuthConfig, OAuthToken
from arcana.types.tool import (
    MCPServerConfig,
    MCPServerStatus,
    MCPTransport,
    ToolStatus,
)
from arcana_cli._async import run_async
from arcana_cli._oauth import probe_oauth, sign_in_or_exit
from arcana_cli._render import EXIT_ERROR, EXIT_NOT_FOUND, truncate
from arcana_cli.command_impl import bearer_value, command_impl
from arcana_cli.constants import AGENTS_BASE, MCPS_PATH
from arcana_cli.ui.renderer import Question, Renderer, View, confirm_or_cancel, fail, lines, renderer_for
from arcana_cli.ui.theme import GREEN, ORANGE, RED, TXT3, dim, hl, make_table, ok, warn

app = typer.Typer(
    help="Manage MCP server connections and their tools (add / list / show / refresh / approve / remove)."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_registry() -> MCPRegistry:
    reg = MCPRegistry(connections_file=MCPS_PATH)
    reg.load()
    return reg


def _resolve_server(r: Renderer, reg: MCPRegistry, name: str) -> MCPServerConfig:
    """Return the named server, or exit ``2`` (not found)."""
    server = reg.get_server(name)
    if server is None:
        fail(r, f"No MCP server named {name!r}.", dim("  Run: arcana mcp list"), code=EXIT_NOT_FOUND)
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


def _auth_display(server: MCPServerConfig) -> str:
    """A redacted one-line auth summary — kind + expiry, never the token itself."""
    if server.auth_type is AuthType.OAUTH:
        token = load_token(server.auth_key_ref) if server.auth_key_ref else None
        if token is None:
            return "OAuth (not signed in)"
        if token.expires_at is None:
            return "OAuth (token in keyring, no expiry)"
        return f"OAuth (token in keyring, expires {token.expires_at.isoformat()})"
    if server.auth_key_ref:
        return f"api_key (keyring ref '{server.auth_key_ref}')"
    return "(none)"


def _server_lines(server: MCPServerConfig) -> list[RenderableType]:
    """Human detail for one server. Only the keyring reference is shown — the
    token itself never leaves the keychain."""
    endpoint = server.server_url if server.transport in _URL_TRANSPORTS else _stdio_repr(server)
    out: list[RenderableType] = [
        f"\n  {hl('Name:')}      {server.name}",
        f"  {hl('Transport:')} {server.transport.value}",
        f"  {hl('Endpoint:')}  {endpoint}",
        f"  {hl('Status:')}    {_status_markup(server.status)}",
        f"  {hl('Auth:')}      {_auth_display(server)}",
    ]
    if server.description:
        out.append(f"  {hl('About:')}     {server.description}")
    out.append("")
    out.append(_tools_table(server) if server.discovered_tools else dim("  No tools discovered."))
    return out


def _server_view(server: MCPServerConfig, *, headline: str | None = None) -> View:
    """One server: its detail lines (under ``headline``, if any) and its ``--json`` detail."""
    human = _server_lines(server)
    return View(lines(*([headline] if headline else []), *human), _server_detail(server))


def _server_summary(server: MCPServerConfig) -> dict[str, Any]:
    return {
        "name": server.name,
        "transport": server.transport.value,
        "auth_type": server.auth_type.value,
        "status": server.status.value,
        "tools": len(server.discovered_tools),
        "changed_tools": sum(1 for t in server.discovered_tools if t.status is ToolStatus.CHANGED),
    }


def _server_detail(server: MCPServerConfig) -> dict[str, Any]:
    token = load_token(server.auth_key_ref) if (server.auth_type is AuthType.OAUTH and server.auth_key_ref) else None
    return {
        "name": server.name,
        "transport": server.transport.value,
        "server_url": server.server_url,
        "command": server.command,
        "args": list(server.args),
        "status": server.status.value,
        "description": server.description,
        "auth_type": server.auth_type.value,
        "auth_key_ref": server.auth_key_ref,  # a reference, never the token
        "token_expires_at": token.expires_at.isoformat() if token and token.expires_at else None,
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


def _validate_server_name(r: Renderer, name: str) -> None:
    if name.lower() in _RESERVED_NAMES:
        fail(r, f"{name!r} is reserved. Choose another server name.")
    if not _NAME_PATTERN.match(name):
        fail(r, f"Invalid server name {name!r}. Use letters, digits, '.', '_', or '-' (no '/' or spaces).")


# The transports a URL server may use (stdio is command-driven, never a URL).
_URL_TRANSPORTS = frozenset({MCPTransport.SSE, MCPTransport.HTTP})


def _infer_transport(r: Renderer, url: str | None, command: str | None, transport: str | None) -> MCPTransport:
    if url and command:
        fail(r, "Pass either --url (HTTP/SSE) or --command (stdio), not both.")
    if not url and not command:
        fail(r, "Provide --url for an HTTP/SSE server or --command for a stdio server.")
    inferred = MCPTransport.SSE if url else MCPTransport.STDIO
    if transport is None:
        return inferred
    try:
        requested = MCPTransport(transport.lower())
    except ValueError:
        fail(r, f"Unknown transport {transport!r}. Use 'http', 'sse', or 'stdio'.")
    if url and requested not in _URL_TRANSPORTS:
        fail(r, f"--transport {requested.value} conflicts with --url (use 'http' or 'sse').")
    if command and requested is not MCPTransport.STDIO:
        fail(r, f"--transport {requested.value} conflicts with --command (stdio).")
    return requested


#: The option that names a keyring reference holding the bearer token, instead of a prompt.
AUTH_KEY_FLAG = "--auth-key"


def _token_problem(answer: str) -> str | None:
    """A validator for the hidden bearer-token question; never quotes the answer."""
    return None if answer.strip() else "No bearer token provided."


async def _bearer_from_header(r: Renderer, headers: list[str]) -> str:
    """Extract the bearer token from a single ``Authorization=...`` header.

    Only ``Authorization`` bearer headers are honoured — the SSE adapter sends
    no others. An empty value is asked as a secret question. The token is never echoed.
    """
    if len(headers) > 1:
        fail(r, "Only a single 'Authorization' header is supported for SSE MCP auth.")
    raw = headers[0]
    if "=" not in raw:
        fail(r, "Invalid --header. Expected 'Authorization=Bearer <token>'.")
    key, value = raw.split("=", 1)
    if key.strip().lower() != "authorization":
        fail(r, f"Unsupported header {key.strip()!r}. SSE MCP auth accepts only 'Authorization'.")
    token = value.strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()
    if not token:
        token = (
            await r.ask(Question("Bearer token", secret=True, validator=_token_problem, flag=AUTH_KEY_FLAG))
        ).strip()
    if not token:
        fail(r, "No bearer token provided.")
    return token


@dataclass
class _AddAuth:
    """The auth decision for ``mcp add``: transport + how the server authenticates."""

    transport: MCPTransport
    auth_type: AuthType
    oauth_config: OAuthConfig | None
    auth_key_ref: str | None


async def _resolve_add_auth(
    r: Renderer,
    *,
    name: str,
    url: str | None,
    transport: MCPTransport,
    explicit_transport: bool,
    header: list[str],
    auth_key: str | None,
    oauth: bool,
    issuer: str | None,
    scope: list[str],
    device: bool,
) -> _AddAuth:
    """Decide the auth path for ``mcp add`` and run the OAuth sign-in if chosen.

    Order of precedence: stdio → scoped env only; an explicit static bearer
    (--header/--auth-key) → api_key; explicit OAuth (--oauth/--issuer) or a
    server that advertises OAuth (probed PRM) → the OAuth flow; otherwise the
    server is added unauthenticated. OAuth persists its token to the keyring and
    defaults the transport to streamable-HTTP unless the user pinned one.
    """
    static_requested = bool(header) or auth_key is not None
    oauth_requested = oauth or issuer is not None

    if transport is MCPTransport.STDIO:
        if static_requested or oauth_requested:
            fail(r, "stdio servers authenticate via scoped env vars, not --header/--auth-key/--oauth.")
        return _AddAuth(MCPTransport.STDIO, AuthType.API_KEY, None, None)

    if static_requested and oauth_requested:
        fail(r, "Pass either a static bearer (--header/--auth-key) or OAuth (--oauth/--issuer), not both.")

    if static_requested:
        return _AddAuth(transport, AuthType.API_KEY, None, await _store_auth(r, name, header, auth_key))

    # OAuth — explicit issuer, or auto-detected from the server's Protected
    # Resource Metadata. The bare default falls back to keyless when nothing is
    # advertised; --oauth makes advertisement mandatory.
    config: OAuthConfig | None = None
    if issuer:
        config = OAuthConfig(issuer=issuer, scopes=scope)
    else:
        probed = await probe_oauth(url) if url else None
        if probed is None:
            if oauth:
                fail(r, "--oauth was requested but the server does not advertise OAuth. Pass --issuer.")
            return _AddAuth(transport, AuthType.API_KEY, None, None)  # keyless
        config = probed.model_copy(update={"scopes": scope}) if scope else probed

    ref = _auth_ref(name)
    token, resolved = await sign_in_or_exit(r, config, device=device, code=EXIT_ERROR)
    _save_oauth_token(r, ref, token)
    # OAuth-authenticated MCP servers ride streamable-HTTP; default to it unless
    # the user explicitly pinned a transport.
    oauth_transport = transport if explicit_transport else MCPTransport.HTTP
    return _AddAuth(oauth_transport, AuthType.OAUTH, resolved, ref)


async def _store_auth(r: Renderer, name: str, headers: list[str], auth_key: str | None) -> str | None:
    """Resolve the server's ``auth_key_ref``, writing any inline token to keyring."""
    if auth_key and headers:
        fail(r, "Pass either --auth-key or --header, not both.")
    if auth_key:
        return auth_key
    if not headers:
        return None
    token = await _bearer_from_header(r, headers)
    ref = _auth_ref(name)
    try:
        keyring.set_password(KEYRING_SERVICE, ref, token)
    except Exception as exc:
        # No OS keyring backend (headless / CI / container). Fail cleanly before
        # anything is persisted — mcps.json is untouched at this point.
        fail(r, f"Could not write the auth token to the OS keyring: {exc}")
    return ref


def _save_oauth_token(r: Renderer, ref: str, token: OAuthToken) -> None:
    try:
        save_token(ref, token)
    except Exception as exc:
        fail(r, f"Could not write the OAuth token to the OS keyring: {exc}")


@app.command("add")
def add_cmd(
    name: str = typer.Option(..., "--name", "-n", help="Server name, e.g. 'notion-mcp'"),
    url: str | None = typer.Option(None, "--url", help="HTTP/SSE endpoint URL"),
    command: str | None = typer.Option(None, "--command", help="stdio server command (implies --transport stdio)"),
    args: list[str] | None = typer.Option(  # noqa: B008
        None, "--arg", help="stdio command argument (repeatable)"
    ),
    transport: str | None = typer.Option(
        None, "--transport", help="http | sse | stdio (inferred from --url / --command)"
    ),
    header: list[str] | None = typer.Option(  # noqa: B008
        None, "--header", help="Static auth 'Authorization=Bearer <token>' — stored in the keyring, never echoed"
    ),
    auth_key: str | None = typer.Option(
        None, "--auth-key", help="Existing keyring reference holding the bearer token (instead of --header)"
    ),
    oauth: bool = typer.Option(False, "--oauth", help="Sign in with OAuth (default when the server advertises it)"),
    issuer: str | None = typer.Option(None, "--issuer", help="OAuth issuer / metadata base (skips 401 auto-detect)"),
    scope: list[str] | None = typer.Option(  # noqa: B008
        None, "--scope", help="OAuth scope to request (repeatable)"
    ),
    device: bool = typer.Option(False, "--device", help="Use the device-code grant (headless / no browser)"),
    description: str = typer.Option("", "--description", "-d", help="Human description"),
    json_: bool = typer.Option(False, "--json", help="Emit the result as JSON"),
) -> None:
    """Register an MCP server, discover its tools, and persist them.

    Transport is inferred: --url → HTTP/SSE, --command → stdio. An HTTP/SSE
    server defaults to **OAuth sign-in** when it advertises OAuth (or with
    --oauth/--issuer); --header/--auth-key opt into a static bearer, and a server
    that advertises neither is added unauthenticated. Auth material goes to the
    OS keyring; mcps.json stores only a reference.
    """
    run_async(
        add_server(
            renderer_for(json_),
            name=name,
            url=url,
            command=command,
            args=args,
            transport=transport,
            header=header,
            auth_key=auth_key,
            oauth=oauth,
            issuer=issuer,
            scope=scope,
            device=device,
            description=description,
        )
    )


@command_impl("mcp add", secrets={"header": bearer_value})
async def add_server(
    r: Renderer,
    *,
    name: str,
    url: str | None,
    command: str | None,
    args: list[str] | None,
    transport: str | None,
    header: list[str] | None,
    auth_key: str | None,
    oauth: bool,
    issuer: str | None,
    scope: list[str] | None,
    device: bool,
    description: str,
) -> None:
    """Register, authenticate and discover one server; asks for a bearer token only when ``--header`` left it blank.

    Nothing is left behind by a run that doesn't finish: a credential this
    command put in the keyring is taken out again if discovery is cancelled
    before the server is registered.
    """
    _validate_server_name(r, name)
    resolved_transport = _infer_transport(r, url, command, transport)
    reg = _load_registry()
    if reg.get_server(name) is not None:
        fail(
            r,
            f"An MCP server named {name!r} already exists.",
            dim(f"  Re-discover with: arcana mcp refresh {name}"),
            dim(f"  Or replace it:    arcana mcp remove {name}"),
        )

    auth = await _resolve_add_auth(
        r,
        name=name,
        url=url,
        transport=resolved_transport,
        explicit_transport=transport is not None,
        header=list(header or []),
        auth_key=auth_key,
        oauth=oauth,
        issuer=issuer,
        scope=list(scope or []),
        device=device,
    )
    cfg = MCPServerConfig(
        name=name,
        transport=auth.transport,
        server_url=url or "",
        command=command,
        args=list(args or []),
        description=description,
        auth_type=auth.auth_type,
        oauth_config=auth.oauth_config,
        auth_key_ref=auth.auth_key_ref,
    )
    try:
        server = await reg.discover(cfg)
    except BaseException:
        # Discovery never raises on its own (an unreachable server is registered
        # as such), so this is a cancellation: don't strand the credential.
        _delete_owned_credential(cfg)
        raise

    r.emit(
        _server_view(server, headline=ok(f"Connected '{name}' — {len(server.discovered_tools)} tool(s) discovered."))
    )

    if server.status is MCPServerStatus.UNREACHABLE:
        r.note(warn(f"Server '{name}' was registered but could not be reached."))
        r.note(dim(f"  Retry with: arcana mcp refresh {name}"))
        raise typer.Exit(EXIT_ERROR)


# ---------------------------------------------------------------------------
# arcana mcp
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(json_: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    """List registered MCP servers."""
    run_async(list_servers(renderer_for(json_)))


@command_impl("mcp list")
async def list_servers(r: Renderer) -> None:
    """Every registered server: a table, or an array of server summaries."""
    servers = _load_registry().list_servers()
    summaries = [_server_summary(s) for s in servers]
    if not servers:
        r.emit(View(dim("No MCP servers connected. Run: arcana mcp add --name <n> --url <url>"), summaries))
        return
    table = make_table("MCP Servers")
    table.add_column("Name", style="bold")
    table.add_column("Transport")
    table.add_column("Tools")
    table.add_column("Status")
    for s in servers:
        table.add_row(s.name, s.transport.value, _tool_count(s), _status_markup(s.status))
    r.emit(View(table, summaries))


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show a server's detail and discovered tools. Secrets are never printed."""
    run_async(show_server(renderer_for(json_), name))


@command_impl("mcp show")
async def show_server(r: Renderer, name: str) -> None:
    """One server's detail and discovered tools."""
    r.emit(_server_view(_resolve_server(r, _load_registry(), name)))


@app.command("refresh")
def refresh_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Re-discover a server's tools and re-run the changed-tool diff."""
    run_async(refresh_server(renderer_for(json_), name))


@command_impl("mcp refresh")
async def refresh_server(r: Renderer, name: str) -> None:
    """Re-discover one server; the result names the tools that changed since the last discovery."""
    reg = _load_registry()
    server = _resolve_server(r, reg, name)
    before = {t.name: t.status for t in server.discovered_tools}
    refreshed = await reg.discover(server)
    newly_changed = [
        t.name
        for t in refreshed.discovered_tools
        if t.status is ToolStatus.CHANGED and before.get(t.name) is not ToolStatus.CHANGED
    ]

    human = _server_lines(refreshed)
    changed_total = sum(1 for t in refreshed.discovered_tools if t.status is ToolStatus.CHANGED)
    if changed_total:
        human.append(warn(f"\n  {changed_total} tool(s) changed and are withheld until approved."))
        human.append(dim(f"  Approve with: arcana mcp approve {name} --all"))
    r.emit(View(lines(*human), {**_server_detail(refreshed), "newly_changed": newly_changed}))

    if refreshed.status is MCPServerStatus.UNREACHABLE:
        r.note(warn(f"Server '{name}' is unreachable — showing last-known tools."))
        raise typer.Exit(EXIT_ERROR)


@app.command("login")
def login_cmd(
    name: str = typer.Argument(..., help="Server name (see: arcana mcp list)"),
    device: bool = typer.Option(False, "--device", help="Use the device-code grant (headless / no browser)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Re-run OAuth sign-in for an existing MCP server.

    Use this when an OAuth server's token has expired and can no longer be
    refreshed — it reuses the server's stored issuer/client, refreshes the keyring
    token, and re-discovers tools. No need to re-`add` the server.
    """
    run_async(login_server(renderer_for(json_), name, device=device))


@command_impl("mcp login")
async def login_server(r: Renderer, name: str, *, device: bool) -> None:
    """Sign an OAuth server in again, store the new token and re-discover its tools."""
    reg = _load_registry()
    server = _resolve_server(r, reg, name)
    if server.auth_type is not AuthType.OAUTH:
        fail(r, f"Server '{name}' does not use OAuth — nothing to sign in to.")
    if server.oauth_config is None:
        fail(
            r,
            f"Server '{name}' has no OAuth config to sign in with.",
            dim(f"  Recreate it with: arcana mcp add --name {name} --url <url> --oauth --issuer <url>"),
        )

    ref = server.auth_key_ref or _auth_ref(name)
    token, resolved = await sign_in_or_exit(r, server.oauth_config, device=device, code=EXIT_ERROR)
    _save_oauth_token(r, ref, token)

    # Persist the (possibly newly-registered) client + ref, then re-discover now
    # that a live token is held.
    server.oauth_config = resolved
    server.auth_key_ref = ref
    refreshed = await reg.discover(server)

    r.emit(
        _server_view(
            refreshed, headline=ok(f"Signed in to '{name}' — {len(refreshed.discovered_tools)} tool(s) discovered.")
        )
    )

    if refreshed.status is MCPServerStatus.UNREACHABLE:
        r.note(warn(f"Server '{name}' was authenticated but could not be reached."))
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
    run_async(approve_server(renderer_for(json_), name, tool=tool, all_=all_))


@command_impl("mcp approve", admits_server=True)
async def approve_server(r: Renderer, name: str, *, tool: list[str] | None, all_: bool) -> None:
    """Approve the named changed tools (or every one with ``all_``), making them resolvable again."""
    reg = _load_registry()
    server = _resolve_server(r, reg, name)

    if all_ and tool:
        fail(r, "Pass either --tool or --all, not both.")
    if not all_ and not tool:
        fail(r, "Specify --tool <name> (repeatable) or --all.")

    targets: list[str] | None
    if all_:
        targets = None
    else:
        targets = []
        for qn in tool or []:
            local = qn.split("/", 1)[1] if "/" in qn else qn
            td = server.get_tool(local)
            if td is None:
                fail(r, f"No tool {local!r} on server {name!r}.", code=EXIT_NOT_FOUND)
            if td.status is not ToolStatus.CHANGED:
                r.note(dim(f"  '{local}' is already active — skipping."))
                continue
            targets.append(local)
        if not targets:
            r.emit(View(dim(f"Nothing to approve on '{name}'."), {"approved": [], "status": server.status.value}))
            return

    approved = reg.approve(name, targets)
    refreshed = _resolve_server(r, reg, name)
    result = {"approved": approved, "status": refreshed.status.value}
    if not approved:
        r.emit(View(dim(f"Nothing to approve on '{name}' — no changed tools matched."), result))
        return
    r.emit(
        View(
            lines(
                ok(f"Approved {len(approved)} tool(s) on '{name}': {', '.join(approved)}"),
                f"  {hl('Status:')} {_status_markup(refreshed.status)}",
            ),
            result,
        )
    )


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
    run_async(remove_server(renderer_for(json_), name, yes=yes, force=force))


@command_impl("mcp remove")
async def remove_server(r: Renderer, name: str, *, yes: bool, force: bool) -> None:
    """Remove one server once confirmed (or with ``yes``); refuses while agents subscribe to it unless ``force``."""
    reg = _load_registry()
    server = _resolve_server(r, reg, name)
    dependents = _dependent_agents(name)

    if dependents and not force:
        r.emit(
            View(
                lines(
                    warn(f"Agents subscribe to tools from '{name}':"),
                    *(f"  {hl(agent_name)}  [{TXT3}]{', '.join(subs)}[/]" for agent_name, subs in dependents),
                    dim("\nRe-run with --force to remove anyway."),
                ),
                # A list (not a dict) so two agents sharing a name both survive.
                {"aborted": "dependents", "dependents": [{"agent": n, "tools": subs} for n, subs in dependents]},
            )
        )
        raise typer.Exit(EXIT_ERROR)

    if not yes:
        await confirm_or_cancel(r, f"Remove MCP server '{name}'?")

    if dependents:
        r.note(warn(f"{len(dependents)} agent(s) will lose these tools until re-subscribed elsewhere."))

    reg.remove_server(name)
    _delete_owned_credential(server)
    r.emit(View(ok(f"MCP server '{name}' removed."), {"removed": name}))
