"""arcana providers — full CRUD for model provider connections.

Every command body is a renderer-agnostic coroutine (``list_providers``,
``show_provider``, ``add_provider``, ``edit_provider``, ``remove_provider``,
``login_provider``): its result is a :class:`~arcana_cli.ui.renderer.Presentable`
(a Rich view and the ``--json`` document of the same data), its errors go
through :func:`~arcana_cli.ui.renderer.fail`, and every question (and an OAuth
sign-in's instructions) goes through the :class:`~arcana_cli.ui.renderer.Renderer`
it is handed and names the option that answers it without a prompt. An API key
is asked as a secret question, so it reaches the keyring and nothing else — no
output, no log, no transcript; neither view ever shows one.
"""

import json
import os
import uuid
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape

from arcana.agents.registry import AgentRegistry
from arcana.models import ConnectionStore, ModelGateway
from arcana.types.auth import AuthType, OAuthConfig
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli._async import run_async
from arcana_cli._oauth import sign_in_or_exit
from arcana_cli.constants import AGENTS_BASE, CONNECTIONS_PATH
from arcana_cli.ui.renderer import (
    Question,
    Renderer,
    View,
    confirm_or_cancel,
    fail,
    lines,
    renderer_for,
    required,
)
from arcana_cli.ui.theme import GREEN, ORANGE, TXT3, dim, hl, make_table, ok, warn

app = typer.Typer(help="Manage model provider connections (list / add / show / edit / remove).")

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

#: The option that supplies an API key without a prompt (the key never goes on the command line).
API_KEY_ENV_FLAG = "--api-key-env"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(r: Renderer, name: str) -> tuple[ConnectionStore, ModelConnection]:
    """Return (store, conn). Exits non-zero if the connection is not found."""
    store = ConnectionStore(CONNECTIONS_PATH)
    conn: ModelConnection | None = store.get_by_name(name)
    if conn is None:
        conn = store.get_by_provider(name.lower())
    if conn is None:
        fail(r, f"No connection found for {name!r}.", dim("  Run: arcana providers list"))
    return store, conn


def _cred_ref(conn: ModelConnection) -> str:
    return conn.credential_ref or f"{conn.id}_api_key"


def _normalise_provider(raw: str) -> str:
    return raw.strip().lower().replace("-", "_")


def _provider_problem(answer: str) -> str | None:
    """A validator for the provider question: one of :data:`_PROVIDERS`, in any case, ``-`` or ``_``."""
    if _normalise_provider(answer) in _PROVIDERS:
        return None
    return f"Unknown provider. Choose from: {', '.join(_PROVIDERS)}"


async def _read_new_key(
    r: Renderer,
    *,
    rotate_key: bool,
    api_key_env: str | None,
    provider_str: str,
) -> str | None:
    if not rotate_key and api_key_env is None:
        return None
    if provider_str not in _CREDENTIAL_PROVIDERS:
        fail(r, f"Provider '{provider_str}' does not use a credential.")
    if api_key_env is not None:
        return _key_from_env(r, api_key_env)
    return await _ask_new_key(r)


async def _ask_new_key(r: Renderer) -> str:
    return await r.ask(Question("New API key", secret=True, flag=API_KEY_ENV_FLAG))


def _key_from_env(r: Renderer, var: str) -> str:
    key = os.environ.get(var)
    if not key:
        fail(r, f"Environment variable {var!r} is not set or empty.")
    return key


async def _run_health_check(r: Renderer, conn: ModelConnection, store: ConnectionStore) -> tuple[str | None, str]:
    """Probe the connection; returns its health (``None`` when unchecked) and the line showing it. Never raises."""
    if not conn.default_model:
        return None, dim("  Skipping health check — no default model configured.")

    model_str = f"{conn.provider}/{conn.default_model}"
    checking = dim(f"  Checking {model_str} ...")
    try:
        async with r.status(f"Checking {model_str} ..."), ModelGateway(connections=store) as gw:
            results = await gw.health(model_str)
            status = next(("healthy" if h.healthy else "down" for h in results.values()), "unknown")
    except Exception:
        status = "down"

    if status == "healthy":
        return status, f"{checking} [{GREEN}]healthy ✓[/]"
    return status, f"{checking} [{ORANGE}]{status} (warning — config saved)[/]"


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


def _warn_default_model(r: Renderer, provider: str) -> None:
    config_path = Path.home() / ".arcana" / "config.json"
    if not config_path.exists():
        return
    try:
        cfg = json.loads(config_path.read_text())
        default_model: str = cfg.get("default_model", "")
        if default_model.startswith(f"{provider}/"):
            r.note(
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


def _connection_summary(conn: ModelConnection) -> dict[str, Any]:
    """The ``--json`` shape of one connection in a list. No secret, and no keyring reference either."""
    return {
        "id": str(conn.id),
        "name": conn.name,
        "provider": str(conn.provider),
        "default_model": conn.default_model,
        "endpoint": conn.endpoint or None,
        "auth_type": conn.auth_type.value,
    }


@app.command("list")
def list_cmd(json_: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    """List all saved model provider connections."""
    run_async(list_providers(renderer_for(json_)))


async def list_providers(r: Renderer) -> None:
    """Every saved connection: a table, or an array of connection summaries."""
    connections = ConnectionStore(CONNECTIONS_PATH).all()
    summaries = [_connection_summary(c) for c in connections]
    if not connections:
        r.emit(View(dim("No connections yet. Run: arcana providers add"), summaries))
        return
    table = make_table("Model Connections")
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Default Model")
    table.add_column("Endpoint", style=TXT3)
    for c in connections:
        table.add_row(c.name, str(c.provider), c.default_model or "(none)", c.endpoint or "(default)")
    r.emit(View(table, summaries))


@app.command("add")
def add_cmd(
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="ollama | anthropic | openai | openai_compat | custom"
    ),
    model_id: str | None = typer.Option(None, "--model-id", "-m", help="Model ID (e.g. hermes-3, claude-sonnet-4-6)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Connection name"),
    endpoint: str | None = typer.Option(None, "--endpoint", "-e", help="Custom base URL"),
    api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key (stored in OS keyring)"),
    api_key_env: str | None = typer.Option(
        None, "--api-key-env", metavar="VAR", help="Read the API key from this environment variable"
    ),
    oauth: bool = typer.Option(False, "--oauth", help="Sign in with OAuth"),
    issuer: str | None = typer.Option(None, "--issuer", help="OAuth issuer / metadata base (implies --oauth)"),
    scope: list[str] | None = typer.Option(  # noqa: B008
        None, "--scope", help="OAuth scope to request (repeatable)"
    ),
    device: bool = typer.Option(False, "--device", help="Use the device-code grant (headless / no browser)"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Overwrite an existing connection of the same name without asking"
    ),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Add or update a model provider connection.

    Defaults to the static API-key path for model providers; pass ``--oauth
    --issuer <url>`` to sign in with OAuth instead. Keyless providers (Ollama)
    need no credential. Tokens and keys go to the OS keyring — never to JSON.
    """
    run_async(
        add_provider(
            renderer_for(json_),
            provider=provider,
            model_id=model_id,
            name=name,
            endpoint=endpoint,
            api_key=api_key,
            api_key_env=api_key_env,
            oauth=oauth,
            issuer=issuer,
            scope=list(scope or []),
            device=device,
            yes=yes,
        )
    )


async def add_provider(
    r: Renderer,
    *,
    provider: str | None,
    model_id: str | None,
    name: str | None,
    endpoint: str | None,
    api_key: str | None,
    api_key_env: str | None,
    oauth: bool,
    issuer: str | None,
    scope: list[str],
    device: bool,
    yes: bool,
) -> None:
    """Add a connection, asking for whatever the flags left out; confirms before overwriting one (unless ``yes``)."""
    if provider is None:
        r.note(dim(f"Providers: {' '.join(_PROVIDERS)}"))
        provider = await r.ask(Question("Provider", validator=_provider_problem, flag="--provider"))

    provider = _normalise_provider(provider)
    if provider not in _PROVIDERS:
        fail(r, f"Unknown provider: {provider!r}. Choose from: {', '.join(_PROVIDERS)}")

    use_oauth = oauth or issuer is not None
    if use_oauth and (api_key is not None or api_key_env is not None):
        fail(r, "Pass either OAuth (--oauth/--issuer) or an API key (--api-key/--api-key-env), not both.")
    if use_oauth and provider not in _CREDENTIAL_PROVIDERS:
        fail(r, f"Provider '{provider}' is keyless — OAuth does not apply.")
    if use_oauth and not issuer:
        fail(r, "OAuth requires --issuer <metadata-url> for a model provider.")

    if model_id is None:
        model_id = await r.ask(
            Question("Model ID (e.g. hermes-3, claude-sonnet-4-6)", validator=required, flag="--model-id")
        )

    if name is None:
        name = await r.ask(
            Question("Connection name", default=f"{provider}/{model_id}", validator=required, flag="--name")
        )

    default_ep = _DEFAULT_ENDPOINTS.get(provider, "")
    if endpoint is None:
        if provider in ("ollama", "openai_compat", "custom"):
            endpoint = await r.ask(Question("Endpoint (base URL)", default=default_ep, flag="--endpoint"))
        else:
            endpoint = default_ep

    resolved_key: str | None = None
    if not use_oauth and provider in _NEEDS_KEY:
        resolved_key = await _read_added_key(r, api_key, api_key_env, provider)

    store = ConnectionStore(CONNECTIONS_PATH)
    existing = store.get_by_name(name)

    if existing is not None:
        if not yes and not await r.confirm(
            f"Connection '{name}' already exists. Overwrite?", default=False, flag="--yes"
        ):
            raise typer.Exit()
        conn_id = existing.id
        action = "Updated"
    else:
        conn_id = uuid.uuid4()
        action = "Added"

    auth_type = AuthType.API_KEY
    oauth_config: OAuthConfig | None = None
    credential_ref: str | None = None

    # Run the OAuth flow *before* writing anything, so a failed/aborted sign-in
    # leaves models.json untouched. The token lands in the keyring; the config
    # (with the resolved client_id) is persisted for later refresh.
    if use_oauth:
        assert issuer is not None  # guarded above
        credential_ref = f"{conn_id}_oauth_token"
        config = OAuthConfig(issuer=issuer, scopes=scope)
        token, resolved = await sign_in_or_exit(r, config, device=device, code=1)
        store.store_token(credential_ref, token)
        auth_type = AuthType.OAUTH
        oauth_config = resolved

    conn = ModelConnection(
        id=conn_id,
        name=name,
        provider=ModelProvider(provider),
        default_model=model_id,
        endpoint=endpoint or "",
        auth_type=auth_type,
        oauth_config=oauth_config,
        credential_ref=credential_ref,
    )

    store.upsert(conn)

    if resolved_key:
        store.set_credential(f"{conn_id}_api_key", resolved_key)

    if use_oauth:
        cred_note = f"  {hl('Auth:')}     [{GREEN}]OAuth — token in OS keyring[/]\n"
    elif resolved_key:
        cred_note = f"  {hl('API key:')}  [{GREEN}]saved to OS keyring[/]\n"
    else:
        cred_note = ""
    details = (
        f"\n  {hl('Provider:')} {provider}\n"
        f"  {hl('Model:')}    {model_id}\n"
        f"  {hl('Endpoint:')} {endpoint or '(provider default)'}\n" + cred_note
    )
    r.emit(
        View(
            "\n" + ok(f"{action} connection '{name}'") + details,
            {**_connection_summary(conn), "action": action.lower()},
        )
    )


async def _read_added_key(r: Renderer, api_key: str | None, api_key_env: str | None, provider: str) -> str | None:
    """Resolve the API key for ``add``: direct flag, env var, or a hidden prompt (blank for none)."""
    if api_key is not None:
        return api_key
    if api_key_env is not None:
        return _key_from_env(r, api_key_env)
    return await r.ask(Question(f"API key for {provider}", default="", secret=True, flag=API_KEY_ENV_FLAG)) or None


@app.command("login")
def login_cmd(
    name: str = typer.Argument(..., help="Connection name (see: arcana providers list)"),
    device: bool = typer.Option(False, "--device", help="Use the device-code grant (headless / no browser)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Re-run OAuth sign-in for an existing connection.

    Use this when an OAuth connection's token has expired and can no longer be
    refreshed — it reuses the connection's stored issuer/client and just refreshes
    the keyring token in place. No need to re-`add` the connection.
    """
    run_async(login_provider(renderer_for(json_), name, device=device))


async def login_provider(r: Renderer, name: str, *, device: bool) -> None:
    """Sign an OAuth connection in again, replacing its keyring token; nothing changes if the sign-in fails."""
    store, conn = _resolve(r, name)
    if conn.auth_type is not AuthType.OAUTH:
        fail(
            r,
            f"Connection '{conn.name}' uses an API key, not OAuth.",
            dim(f"  Rotate its key with: arcana providers edit {conn.name} --rotate-key"),
        )
    if conn.oauth_config is None:
        fail(
            r,
            f"Connection '{conn.name}' has no OAuth config to sign in with.",
            dim("  Recreate it with: arcana providers add ... --oauth --issuer <url>"),
        )

    ref = conn.credential_ref or f"{conn.id}_oauth_token"
    token, resolved = await sign_in_or_exit(r, conn.oauth_config, device=device, code=1)
    store.store_token(ref, token)
    updated = conn.model_copy(update={"oauth_config": resolved, "credential_ref": ref})
    store.upsert(updated)
    r.emit(
        View(
            ok(f"Signed in to '{conn.name}' — token refreshed in the OS keyring."),
            {**_connection_summary(updated), "signed_in": True},
        )
    )


@app.command("show")
def show_cmd(
    name: str = typer.Argument(..., help="Connection name (see: arcana providers list)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show a connection's details. Secrets are never printed."""
    run_async(show_provider(renderer_for(json_), name))


async def show_provider(r: Renderer, name: str) -> None:
    """One connection's details; the credential is the same redacted summary in both views."""
    store, conn = _resolve(r, name)

    headers_display = ", ".join(f"{k}: {v}" for k, v in conn.headers.items()) if conn.headers else "(none)"
    cred_display = _credential_display(store, conn)

    human = lines(
        f"\n  {hl('Name:')}          {conn.name}",
        f"  {hl('Provider:')}      {conn.provider}",
        f"  {hl('Default Model:')} {conn.default_model or '(none)'}",
        f"  {hl('Endpoint:')}      {conn.endpoint or '(provider default)'}",
        f"  {hl('Headers:')}       {headers_display}",
        f"  {hl('Auth type:')}     {conn.auth_type.value}",
        f"  {hl('Credential:')}    {cred_display}",
        f"  {hl('Created:')}       {conn.created_at.isoformat()}",
        f"  {hl('Updated:')}       {conn.updated_at.isoformat()}",
        "",
    )
    detail = {
        **_connection_summary(conn),
        "headers": dict(conn.headers),
        "credential": cred_display,
        "created_at": conn.created_at.isoformat(),
        "updated_at": conn.updated_at.isoformat(),
    }
    r.emit(View(human, detail))


def _credential_display(store: ConnectionStore, conn: ModelConnection) -> str:
    """A redacted one-line credential summary — auth kind and expiry, never a secret."""
    if conn.auth_type is AuthType.OAUTH:
        ref = conn.credential_ref
        token = store.get_token(ref) if ref else None
        if token is None:
            return "OAuth (not signed in)"
        if token.expires_at is None:
            return "OAuth (token in keyring, no expiry)"
        return f"OAuth (token in keyring, expires {token.expires_at.isoformat()})"
    has_key = bool(store.get_api_key(conn.id))
    return f"API key in keyring ({_cred_ref(conn)})" if has_key else "(none)"


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
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Edit an existing model connection's mutable fields.

    Runs interactively by default — blank input preserves the current value.
    Use flags for scriptable / non-interactive edits.

    provider and adapter_type are immutable. To change them, remove and recreate:
      arcana providers remove <name>
      arcana providers add
    """
    run_async(
        edit_provider(
            renderer_for(json_),
            name,
            base_url=base_url,
            rotate_key=rotate_key,
            api_key_env=api_key_env,
            header=header,
            no_verify=no_verify,
        )
    )


async def edit_provider(
    r: Renderer,
    name: str,
    *,
    base_url: str | None,
    rotate_key: bool,
    api_key_env: str | None,
    header: list[str] | None,
    no_verify: bool,
) -> None:
    """Edit a connection: from the flags when any is given, otherwise by asking (blank keeps the current value)."""
    store, conn = _resolve(r, name)
    provider_str = str(conn.provider)

    flag_driven = any([base_url is not None, rotate_key, api_key_env is not None, header is not None])

    new_endpoint: str = conn.endpoint
    new_headers: dict[str, str] = dict(conn.headers)
    new_key: str | None = None

    if not flag_driven:
        r.note(f"\n{hl('Editing:')} {conn.name}  {dim(escape(f'[{provider_str}]'))}")
        r.note(dim("Press Enter to keep the current value.\n"))

        new_endpoint = await r.ask(Question("Endpoint (base URL)", default=conn.endpoint or "", flag="--base-url"))

        if provider_str in _CREDENTIAL_PROVIDERS and await r.confirm(
            "Rotate API key?", default=False, flag=API_KEY_ENV_FLAG
        ):
            new_key = await _ask_new_key(r)

        if provider_str == "custom" and conn.headers:
            r.note(dim(f"\n  Current headers: {', '.join(f'{k}: {v}' for k, v in conn.headers.items())}"))
            r.note(dim("  Use --header to modify headers non-interactively."))
    else:
        if base_url is not None:
            new_endpoint = base_url

        new_key = await _read_new_key(
            r,
            rotate_key=rotate_key,
            api_key_env=api_key_env,
            provider_str=provider_str,
        )

        if header is not None:
            if provider_str != "custom":
                fail(r, "Custom headers are only supported for the 'custom' adapter type.")
            new_headers = {}
            for h in header:
                if ":" not in h:
                    fail(r, f"Invalid header format {h!r}. Expected 'Key: Value'.")
                k, v = h.split(":", 1)
                new_headers[k.strip()] = v.strip()

    # Credential rotation: write new key before touching models.json
    if new_key:
        ref = _cred_ref(conn)
        try:
            store.set_credential(ref, new_key)
        except Exception as exc:
            fail(r, f"Failed to write credential to keyring: {exc}", dim("  models.json was not modified."))
        conn = conn.model_copy(update={"credential_ref": ref})

    updated = conn.model_copy(update={"endpoint": new_endpoint, "headers": new_headers})
    store.upsert(updated)

    human: list[str] = ["\n" + ok(f"Connection '{conn.name}' updated.")]
    health: str | None = None
    if not no_verify:
        health, health_line = await _run_health_check(r, updated, store)
        human.append(health_line)
    human.append("")
    r.emit(View(lines(*human), {**_connection_summary(updated), "health": health}))


@app.command("remove")
def remove_cmd(
    name: str = typer.Argument(..., help="Connection name (see: arcana providers list)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    force: bool = typer.Option(False, "--force", help="Remove even if dependent agents exist"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Remove a model provider connection and its stored credential.

    Scans for dependent agents and aborts unless --force is given.
    """
    run_async(remove_provider(renderer_for(json_), name, yes=yes, force=force))


async def remove_provider(r: Renderer, name: str, *, yes: bool, force: bool) -> None:
    """Remove a connection once confirmed (or with ``yes``); refuses while agents depend on it unless ``force``."""
    store, conn = _resolve(r, name)
    provider_str = str(conn.provider)
    conn_name = conn.name

    agent_registry = AgentRegistry(AGENTS_BASE)
    agents = agent_registry.list()
    dependents = [a for a in agents if _agent_targets_connection(a.model, provider_str, conn_name)]

    if dependents and not force:
        r.emit(
            View(
                lines(
                    warn(f"The following agents depend on '{conn.name}':"),
                    *(f"  {hl(a.name)}  [{TXT3}]{a.model}[/]" for a in dependents),
                    dim("\nRe-run with --force to remove anyway."),
                ),
                {
                    "aborted": "dependents",
                    "dependents": [{"agent": a.name, "id": str(a.id), "model": a.model} for a in dependents],
                },
            )
        )
        raise typer.Exit(1)

    if not yes:
        await confirm_or_cancel(r, f"Remove connection '{conn.name}'?")

    if dependents:
        r.note(warn(_removal_consequence(provider_str)))

    store.delete(conn.name)

    _warn_default_model(r, provider_str)

    r.emit(View(ok(f"Connection '{conn.name}' removed."), {"removed": conn.name}))
