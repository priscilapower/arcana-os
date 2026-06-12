"""arcana connect — manage model connections."""

import uuid

import keyring
import typer
from rich.console import Console

from arcana.models import ConnectionStore
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.constants import CONNECTIONS_PATH
from arcana_cli.ui.theme import GREEN, TXT3, dim, err, hl, make_table, ok

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

    conn_id = uuid.uuid4()
    conn = ModelConnection(
        id=conn_id,
        name=name,
        provider=ModelProvider(provider),
        model_id=model_id,
        endpoint=endpoint or "",
    )

    if existing is not None:
        if not typer.confirm(f"Connection '{name}' already exists. Overwrite?"):
            raise typer.Exit()
        action = "Updated"
    else:
        action = "Added"

    store.upsert(conn)

    if api_key:
        keyring.set_password("arcana", f"{conn_id}_api_key", api_key)

    key_note = f"  {hl('API key:')}  [{GREEN}]saved to OS keyring[/]\n" if api_key else ""
    details = (
        f"\n  {hl('Provider:')} {provider}\n"
        f"  {hl('Model:')}    {model_id}\n"
        f"  {hl('Endpoint:')} {endpoint or '(provider default)'}\n" + key_note
    )
    console.print("\n" + ok(f"{action} connection '{name}'") + details)


@app.command("list")
def list_cmd() -> None:
    """List all saved model connections."""
    connections = ConnectionStore(CONNECTIONS_PATH).all()
    if not connections:
        console.print(dim("No connections yet. Run: arcana connect model"))
        return
    table = make_table("Model Connections")
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Model ID")
    table.add_column("Endpoint", style=TXT3)
    for c in connections:
        table.add_row(c.name, str(c.provider), c.model_id, c.endpoint or "(default)")
    console.print(table)
