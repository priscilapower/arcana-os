"""arcana connect — manage model connections."""

import json
import uuid
from typing import Any

import keyring
import typer
from rich.console import Console
from rich.table import Table

from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.constants import CONNECTIONS_PATH

app = typer.Typer(help="Manage connections to models and services.")
console = Console()

_PROVIDERS = ["ollama", "anthropic", "openai", "openai_compat", "custom"]
_DEFAULT_ENDPOINTS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "anthropic": "",
    "openai": "https://api.openai.com/v1",
    "openai_compat": "",
    "custom": "",
}
_NEEDS_KEY = {"anthropic", "openai", "openai_compat", "custom"}


def _load() -> list[dict[str, Any]]:
    if CONNECTIONS_PATH.exists():
        return json.loads(CONNECTIONS_PATH.read_text())  # type: ignore[no-any-return]
    return []


def _save(connections: list[dict[str, Any]]) -> None:
    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_PATH.write_text(json.dumps(connections, indent=2))


@app.command("model")
def model_cmd(
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="ollama | anthropic | openai | openai_compat | custom"
    ),
    model_id: str | None = typer.Option(None, "--model-id", "-m", help="Model ID (e.g. hermes-3, claude-sonnet-4-6)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Connection name"),
    endpoint: str | None = typer.Option(None, "--endpoint", "-e", help="Custom base URL"),
    api_key: str | None = typer.Option(None, "--api-key", "-k", help="API key (stored in OS keyring)"),
) -> None:
    """Add or update a model connection (stored in ~/.arcana/connections/models.json)."""
    if provider is None:
        console.print(f"[dim]Providers: {' '.join(_PROVIDERS)}[/dim]")
        provider = str(typer.prompt("Provider"))

    provider = provider.lower().replace("-", "_")
    if provider not in _PROVIDERS:
        console.print(f"[red]Unknown provider: {provider!r}. Choose from: {', '.join(_PROVIDERS)}[/red]")
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

    conn_id = uuid.uuid4()
    conn = ModelConnection(
        id=conn_id,
        name=name,
        provider=ModelProvider(provider),
        model_id=model_id,
        endpoint=endpoint or "",
    )

    connections = _load()
    existing_idx = next((i for i, c in enumerate(connections) if c.get("name") == name), None)
    conn_dict: dict[str, Any] = json.loads(conn.model_dump_json())

    if existing_idx is not None:
        if not typer.confirm(f"Connection '{name}' already exists. Overwrite?"):
            raise typer.Exit()
        connections[existing_idx] = conn_dict
        action = "Updated"
    else:
        connections.append(conn_dict)
        action = "Added"

    _save(connections)

    if api_key:
        keyring.set_password("arcana", f"{conn_id}_api_key", api_key)

    key_note = "  API key:  [green]saved to OS keyring[/green]\n" if api_key else ""
    console.print(
        f"\n[bold green]✓ {action} connection '{name}'[/bold green]\n"
        f"  Provider: {provider}\n"
        f"  Model:    {model_id}\n"
        f"  Endpoint: {endpoint or '(provider default)'}\n" + key_note
    )


@app.command("list")
def list_cmd() -> None:
    """List all saved model connections."""
    connections = _load()
    if not connections:
        console.print("[dim]No connections yet. Run: arcana connect model[/dim]")
        return
    table = Table(title="Model Connections", show_header=True, header_style="bold magenta")
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Model ID")
    table.add_column("Endpoint", style="dim")
    for c in connections:
        name_val: str = c.get("name") or ""
        provider_val: str = c.get("provider") or ""
        model_val: str = c.get("model_id") or ""
        endpoint_val: str = c.get("endpoint") or "(default)"
        table.add_row(name_val, provider_val, model_val, endpoint_val)
    console.print(table)
