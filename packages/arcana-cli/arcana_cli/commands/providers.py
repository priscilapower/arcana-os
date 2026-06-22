"""arcana providers — full CRUD for model provider connections."""

import json
import os
import uuid
from pathlib import Path

import typer
from rich.console import Console

from arcana.models import ConnectionStore
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.constants import AGENTS_BASE, CONNECTIONS_PATH
from arcana_cli.ui.theme import GREEN, ORANGE, TXT3, dim, err, hl, make_table, ok, warn

app = typer.Typer(help="Manage model provider connections (list / add / show / edit / remove).")
console = Console()

_PROVIDERS = ["ollama", "anthropic", "openai", "openai_compat", "custom"]
_DEFAULT_ENDPOINTS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "anthropic": "",
    "openai": "https://api.openai.com/v1",
    "openai_compat": "",
    "custom": "",
}
_NEEDS_KEY: frozenset[str] = frozenset({"anthropic", "openai", "openai_compat", "custom"})
_CREDENTIAL_PROVIDERS: frozenset[str] = _NEEDS_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(name: str) -> tuple[ConnectionStore, ModelConnection]:
    """Return (store, conn). Exits non-zero if the connection is not found."""
    store = ConnectionStore(CONNECTIONS_PATH)
    conn: ModelConnection | None = store.get_by_name(name)
    if conn is None:
        conn = store.get_by_provider(name.lower())
    if conn is None:
        console.print(err(f"No connection found for {name!r}."))
        console.print(dim("  Run: arcana providers list"))
        raise typer.Exit(1)
    return store, conn


def _cred_ref(conn: ModelConnection) -> str:
    return conn.credential_ref or f"{conn.id}_api_key"


def _read_new_key(
    *,
    rotate_key: bool,
    api_key_env: str | None,
    provider_str: str,
) -> str | None:
    if not rotate_key and api_key_env is None:
        return None
    if provider_str not in _CREDENTIAL_PROVIDERS:
        console.print(err(f"Provider '{provider_str}' does not use a credential."))
        raise typer.Exit(1)
    if api_key_env is not None:
        key = os.environ.get(api_key_env)
        if not key:
            console.print(err(f"Environment variable {api_key_env!r} is not set or empty."))
            raise typer.Exit(1)
        return key
    return typer.prompt("New API key", hide_input=True)


def _run_health_check(conn: ModelConnection, store: ConnectionStore) -> None:
    """Probe the connection and print healthy / down. Never raises."""
    import asyncio

    from arcana.models.gateway import ModelGateway

    if not conn.default_model:
        console.print(dim("  Skipping health check — no default model configured."))
        return

    model_str = f"{conn.provider}/{conn.default_model}"
    console.print(dim(f"  Checking {model_str} ..."), end="")
    try:

        async def _probe() -> str:
            async with ModelGateway(connections=store) as gw:
                results = await gw.health(model_str)
                for h in results.values():
                    return "healthy" if h.healthy else "down"
            return "unknown"

        status = asyncio.run(_probe())
    except Exception:
        status = "down"

    if status == "healthy":
        console.print(f" [{GREEN}]healthy ✓[/]")
    else:
        console.print(f" [{ORANGE}]{status} (warning — config saved)[/]")


def _removal_consequence(provider: str) -> str:
    if provider == "ollama":
        return (
            "Dependent agents will revert to the ProviderRegistry default "
            "(localhost:11434). This is a soft reset unless a custom endpoint was set."
        )
    if provider in ("anthropic", "openai", "openai_compat"):
        return (
            f"Dependent agents will fail at call time with a credential error - "
            f"no key to rebuild from for '{provider}'."
        )
    return (
        f"Dependent agents will fail - no registry default exists for '{provider}'. "
        f"Recreate the connection to restore them."
    )


def _warn_default_model(provider: str) -> None:
    config_path = Path.home() / ".arcana" / "config.json"
    if not config_path.exists():
        return
    try:
        cfg = json.loads(config_path.read_text())
        default_model: str = cfg.get("default_model", "")
        if default_model.startswith(f"{provider}/"):
            console.print(
                warn(
                    f"config.default_model is '{default_model}', which references the removed "
                    f"provider. Run: arcana config set default_model <new-model>"
                )
            )
    except Exception:
        pass


def _agent_targets_connection(model: str, provider_str: str, conn_name: str) -> bool:
    """Return True if an agent's model reference targets this connection."""
    return (
        model.startswith(f"{provider_str}:{conn_name}/")
        or model == f"{provider_str}:{conn_name}"
        or model.startswith(f"{provider_str}/")
        or model == provider_str
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd() -> None:
    """List all saved model provider connections."""
    connections = ConnectionStore(CONNECTIONS_PATH).all()
    if not connections:
        console.print(dim("No connections yet. Run: arcana providers add"))
        return
    table = make_table("Model Connections")
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Default Model")
    table.add_column("Endpoint", style=TXT3)
    for c in connections:
        table.add_row(c.name, str(c.provider), c.default_model or "(none)", c.endpoint or "(default)")
    console.print(table)


@app.command("add")
def add_cmd(
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="ollama | anthropic | openai | openai_compat | custom"
    ),
    model_id: str | None = typer.Option(None, "--model-id", "-m", help="Model ID (e.g. hermes-3, claude-sonnet-4-6)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Connection name"),
    endpoint: str | None = typer.Option(None, "--endpoint", "-e", help="Custom base URL"),
    api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key (stored in OS keyring)"),
) -> None:
    """Add or update a model provider connection."""
    if provider is None:
        console.print(dim(f"Providers: {' '.join(_PROVIDERS)}"))
        provider = str(typer.prompt("Provider"))

    provider = provider.lower().replace("-", "_")
    if provider not in _PROVIDERS:
        console.print(err(f"Unknown provider: {provider!r}. Choose from: {', '.join(_PROVIDERS)}"))
        raise typer.Exit(1)

    if model_id is None:
        model_id = str(typer.prompt("Model ID (e.g. hermes-3, claude-sonnet-4-6)"))

    if name is None:
        name = str(typer.prompt("Connection name", default=f"{provider}/{model_id}"))

    default_ep = _DEFAULT_ENDPOINTS.get(provider, "")
    if endpoint is None:
        if provider in ("ollama", "openai_compat", "custom"):
            endpoint = str(typer.prompt("Endpoint (base URL)", default=default_ep))
        else:
            endpoint = default_ep

    if api_key is None and provider in _NEEDS_KEY:
        api_key = str(typer.prompt(f"API key for {provider}", hide_input=True, default=""))

    store = ConnectionStore(CONNECTIONS_PATH)
    existing = store.get_by_name(name)

    if existing is not None:
        if not typer.confirm(f"Connection '{name}' already exists. Overwrite?"):
            raise typer.Exit()
        conn_id = existing.id
        action = "Updated"
    else:
        conn_id = uuid.uuid4()
        action = "Added"

    conn = ModelConnection(
        id=conn_id,
        name=name,
        provider=ModelProvider(provider),
        default_model=model_id,
        endpoint=endpoint or "",
    )

    store.upsert(conn)

    if api_key:
        import keyring

        ref = f"{conn_id}_api_key"
        keyring.set_password("arcana", ref, api_key)

    key_note = f"  {hl('API key:')}  [{GREEN}]saved to OS keyring[/]\n" if api_key else ""
    details = (
        f"\n  {hl('Provider:')} {provider}\n"
        f"  {hl('Model:')}    {model_id}\n"
        f"  {hl('Endpoint:')} {endpoint or '(provider default)'}\n" + key_note
    )
    console.print("\n" + ok(f"{action} connection '{name}'") + details)


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Connection name (see: arcana providers list)"),
) -> None:
    """Show a connection's details. Secrets are never printed."""
    store, conn = _resolve(name)

    has_key = bool(store.get_api_key(conn.id))
    cred_display = f"stored in keyring ({_cred_ref(conn)})" if has_key else "(none)"
    headers_display = ", ".join(f"{k}: {v}" for k, v in conn.headers.items()) if conn.headers else "(none)"

    console.print(f"\n  {hl('Name:')}          {conn.name}")
    console.print(f"  {hl('Provider:')}      {conn.provider}")
    console.print(f"  {hl('Default Model:')} {conn.default_model or '(none)'}")
    console.print(f"  {hl('Endpoint:')}      {conn.endpoint or '(provider default)'}")
    console.print(f"  {hl('Headers:')}       {headers_display}")
    console.print(f"  {hl('Credential:')}    {cred_display}")
    console.print(f"  {hl('Created:')}       {conn.created_at.isoformat()}")
    console.print(f"  {hl('Updated:')}       {conn.updated_at.isoformat()}")
    console.print()


@app.command("edit")
def edit_cmd(
    name: str = typer.Argument(..., help="Connection name (see: arcana providers list)"),
    base_url: str | None = typer.Option(None, "--base-url", help="New base URL / endpoint"),
    rotate_key: bool = typer.Option(
        False, "--rotate-key", help="Rotate the stored API key (interactive hidden prompt)"
    ),
    api_key_env: str | None = typer.Option(
        None,
        "--api-key-env",
        metavar="VAR",
        help="Read new API key from this environment variable",
    ),
    header: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--header",
        help="Set a custom header as 'Key: Value' (repeatable; custom adapter only)",
    ),
    no_verify: bool = typer.Option(False, "--no-verify", help="Skip post-edit health check"),
) -> None:
    """Edit an existing model connection's mutable fields.

    Runs interactively by default — blank input preserves the current value.
    Use flags for scriptable / non-interactive edits.

    provider and adapter_type are immutable. To change them, remove and recreate:
      arcana providers remove <name>
      arcana providers add
    """
    store, conn = _resolve(name)
    provider_str = str(conn.provider)

    flag_driven = any([base_url is not None, rotate_key, api_key_env is not None, header is not None])

    new_endpoint: str = conn.endpoint
    new_headers: dict[str, str] = dict(conn.headers)
    new_key: str | None = None

    if not flag_driven:
        console.print(f"\n{hl('Editing:')} {conn.name}  {dim(f'[{provider_str}]')}")
        console.print(dim("Press Enter to keep the current value.\n"))

        prompted = typer.prompt("Endpoint (base URL)", default=conn.endpoint or "")
        new_endpoint = prompted

        if provider_str in _CREDENTIAL_PROVIDERS:
            if typer.confirm("Rotate API key?", default=False):
                new_key = typer.prompt("New API key", hide_input=True)

        if provider_str == "custom" and conn.headers:
            console.print(dim(f"\n  Current headers: {', '.join(f'{k}: {v}' for k, v in conn.headers.items())}"))
            console.print(dim("  Use --header to modify headers non-interactively."))
    else:
        if base_url is not None:
            new_endpoint = base_url

        new_key = _read_new_key(
            rotate_key=rotate_key,
            api_key_env=api_key_env,
            provider_str=provider_str,
        )

        if header is not None:
            if provider_str != "custom":
                console.print(err("Custom headers are only supported for the 'custom' adapter type."))
                raise typer.Exit(1)
            new_headers = {}
            for h in header:
                if ":" not in h:
                    console.print(err(f"Invalid header format {h!r}. Expected 'Key: Value'."))
                    raise typer.Exit(1)
                k, v = h.split(":", 1)
                new_headers[k.strip()] = v.strip()

    # Credential rotation: write new key before touching models.json
    if new_key:
        ref = _cred_ref(conn)
        try:
            store.set_credential(ref, new_key)
        except Exception as exc:
            console.print(err(f"Failed to write credential to keyring: {exc}"))
            console.print(dim("  models.json was not modified."))
            raise typer.Exit(1) from exc
        conn = conn.model_copy(update={"credential_ref": ref})

    updated = conn.model_copy(update={"endpoint": new_endpoint, "headers": new_headers})
    store.upsert(updated)

    console.print("\n" + ok(f"Connection '{conn.name}' updated."))

    if not no_verify:
        _run_health_check(updated, store)

    console.print()


@app.command("remove")
def remove_cmd(
    name: str = typer.Argument(..., help="Connection name (see: arcana providers list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    force: bool = typer.Option(False, "--force", help="Remove even if dependent agents exist"),
) -> None:
    """Remove a model provider connection and its stored credential.

    Scans for dependent agents and aborts unless --force is given.
    """
    from arcana.agents.registry import AgentRegistry

    store, conn = _resolve(name)
    provider_str = str(conn.provider)
    conn_name = conn.name

    agent_registry = AgentRegistry(AGENTS_BASE)
    agents = agent_registry.list()
    dependents = [a for a in agents if _agent_targets_connection(a.model, provider_str, conn_name)]

    if dependents and not force:
        console.print(warn(f"The following agents depend on '{conn.name}':"))
        for a in dependents:
            console.print(f"  {hl(a.name)}  [{TXT3}]{a.model}[/]")
        console.print(dim("\nRe-run with --force to remove anyway."))
        raise typer.Exit(1)

    if not yes:
        typer.confirm(f"Remove connection '{conn.name}'?", abort=True)

    if dependents:
        console.print(warn(_removal_consequence(provider_str)))

    store.delete(conn.name)

    _warn_default_model(provider_str)

    console.print(ok(f"Connection '{conn.name}' removed."))
