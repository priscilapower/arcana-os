"""Agent management commands."""

from typing import Any
from uuid import UUID

import typer
from rich.console import Console

from arcana.agents.registry import AgentRegistry
from arcana.cards.engine import BlendCompatibility, CardEngine
from arcana.cards.registry import CardRegistry, get_registry
from arcana.models.connection_store import ConnectionStore
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana_cli.constants import AGENTS_BASE, CONNECTIONS_PATH, ROMAN
from arcana_cli.ui.card_picker import select_card, select_cards
from arcana_cli.ui.theme import (
    GREEN,
    TXT3,
    dim,
    err,
    hl,
    make_panel,
    make_panel_fit,
    make_table,
    ok,
    status_markup,
    warn,
)

app = typer.Typer(help="Manage agents.")
console = Console()


def _registry() -> AgentRegistry:
    return AgentRegistry(AGENTS_BASE)


def _store() -> ConnectionStore:
    return ConnectionStore(CONNECTIONS_PATH)


def _validate_card(raw: str) -> Card:
    for candidate in (raw, f"the-{raw}"):
        try:
            return Card(candidate)
        except ValueError:
            pass
    matches = [c for c in Card if raw.lower() in c.value]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Unknown card: {raw!r}")


def _resolve_agent(name_or_id: str) -> AgentRecord:
    """Resolve a name or UUID string to an AgentRecord, with ambiguity detection."""
    reg = _registry()
    try:
        uid = UUID(name_or_id)
        record = reg.get(uid)
        if record is not None and not record.is_archived:
            return record
        console.print(err(f"No agent with ID '{name_or_id}'."))
        raise typer.Exit(1)
    except ValueError:
        pass
    matches = [a for a in reg.list() if a.name == name_or_id]
    if not matches:
        console.print(err(f"No agent '{name_or_id}'."))
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(err(f"Ambiguous name '{name_or_id}'. Use one of these IDs:"))
        for a in matches:
            console.print(f"  {a.id}")
        raise typer.Exit(1)
    return matches[0]


def _pick_connection(default_name: str | None = None) -> tuple[UUID, str]:
    """Show available connections and prompt the user to pick one."""
    store = _store()
    connections = store.all()
    if not connections:
        console.print(err("No model connections configured. Run: arcana connect model"))
        raise typer.Exit(1)

    table = make_table("Model Connections")
    table.add_column("#", style=TXT3, width=4)
    table.add_column("Name", style="bold")
    table.add_column("Provider")
    table.add_column("Model ID")
    for i, c in enumerate(connections, 1):
        table.add_row(str(i), c.name, str(c.provider), c.model_id)
    console.print(table)

    prompt_default = default_name or connections[0].name
    choice = typer.prompt("Choose a connection (name or #)", default=prompt_default)

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(connections):
            conn = connections[idx]
            return conn.id, conn.name
        msg = f"Invalid selection. Enter a number between 1 and {len(connections)}, or a connection name."
        console.print(err(msg))
        raise typer.Exit(1)
    except ValueError:
        pass

    conn = store.get_by_name(choice)
    if conn is None:
        console.print(err(f"No connection named '{choice}'."))
        raise typer.Exit(1)
    return conn.id, conn.name


def _print_compat(compat: BlendCompatibility, registry: CardRegistry) -> None:
    """Print tension/synergy warnings after modifier selection. Non-blocking."""
    if compat.has_tensions:
        console.print(warn(f"\n  ✦ CLASH — {len(compat.tensions)} tension(s) in this blend:"))
        for a, b in compat.tensions:
            console.print(warn(f"    ✗  {registry.get(a).name} ↔ {registry.get(b).name}"))
    if compat.has_synergies:
        console.print(dim(f"\n  ✦ SYNERGY — {len(compat.synergies)} synergy/ies in this blend:"))
        for a, b in compat.synergies:
            console.print(dim(f"    ✓  {registry.get(a).name} + {registry.get(b).name}"))
    if compat.has_tensions or compat.has_synergies:
        console.print()


@app.command("create")
def create(
    name: str = typer.Option(None, "--name", "-n", help="Agent name"),
    card: str = typer.Option(None, "--card", "-c", help="Card id (e.g. 'hermit')"),
    model: str = typer.Option(None, "--model", "-m", help="Model connection name"),
) -> None:
    """Create a new agent. Interactive if no flags provided."""
    if not name:
        name = typer.prompt("Agent name")

    modifier_cards: list[Card] = []

    if not card:
        card_enum = select_card("Choose a primary card for this agent")
        if card_enum is None:
            raise typer.Exit()
        if card_enum == Card.WORLD:
            console.print(err("The World is reserved and cannot be assigned."))
            raise typer.Exit(1)
        if typer.confirm("Blend with modifier cards?", default=False):
            raw_modifiers = select_cards(
                "Select modifier cards (Space to toggle, Enter to confirm)",
                initial=[],
                max_items=CardEngine.MAX_MODIFIERS,
                exclude={card_enum, Card.WORLD},
            )
            modifier_cards = [m for m in raw_modifiers if m != card_enum]
            if modifier_cards:
                _print_compat(
                    CardEngine(get_registry()).check_compatibility(card_enum, modifier_cards),
                    get_registry(),
                )
    else:
        try:
            card_enum = _validate_card(card)
        except ValueError as exc:
            console.print(err(str(exc)))
            raise typer.Exit(1) from exc
        if card_enum == Card.WORLD:
            console.print(err("The World is reserved and cannot be assigned."))
            raise typer.Exit(1)

    if model:
        conn = _store().get_by_name(model)
        if conn is None:
            console.print(err(f"No connection named '{model}'. Run: arcana connect list"))
            raise typer.Exit(1)
        conn_id, conn_name = conn.id, conn.name
    else:
        conn_id, conn_name = _pick_connection()

    registry = get_registry()
    record = _registry().create(
        name=name,
        card=card_enum,
        model_connection_id=conn_id,
        modifier_cards=modifier_cards,
    )
    tarot = registry.get(card_enum)
    modifier_str = (
        f"  {hl('Modifiers:')} " + ", ".join(registry.get(m).name for m in modifier_cards) + "\n"
        if modifier_cards
        else ""
    )
    console.print(
        make_panel_fit(
            f"[bold {GREEN}]Agent '{record.name}' created.[/]\n\n"
            f"  {hl('ID:')}    [{TXT3}]{record.id}[/]\n"
            f"  {hl('Card:')}  {ROMAN[tarot.number]} · {tarot.name} — {tarot.archetype.role}\n"
            + modifier_str
            + f"  {hl('Model:')} {conn_name}\n"
            f"  {hl('Temp:')}  {record.temperature:.2f}",
            title="New Agent",
            card=card_enum,
        )
    )


@app.command("list")
def list_agents() -> None:
    """List all registered agents."""
    records = _registry().list()
    if not records:
        console.print(dim("No agents yet. Run: arcana agent create"))
        return

    conn_map = {c.id: c for c in _store().all()}
    table = make_table("Agents")
    table.add_column("Name", style="bold")
    table.add_column("Card")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("ID", style=TXT3)
    for r in records:
        conn = conn_map.get(r.model_connection_id)
        model_name = conn.name if conn else str(r.model_connection_id)[:8] + "…"
        table.add_row(r.name, r.card.value, model_name, status_markup(r.status.value), str(r.id)[:8] + "…")
    console.print(table)


@app.command("show")
def show(name: str = typer.Argument(..., help="Agent name or UUID")) -> None:
    """Show full config for an agent."""
    record = _resolve_agent(name)
    conn_map = {c.id: c for c in _store().all()}
    conn = conn_map.get(record.model_connection_id)
    model_label = conn.name if conn else str(record.model_connection_id)

    modifier_str = ", ".join(c.value for c in record.modifier_cards) or "none"
    tags_str = ", ".join(record.tags) or "none"
    prompt_preview = record.system_prompt[:200] + ("…" if len(record.system_prompt) > 200 else "")

    console.print(
        make_panel(
            f"{hl('ID:')}          [{TXT3}]{record.id}[/]\n"
            f"{hl('Name:')}        {record.name}\n"
            f"{hl('Description:')} {record.description or '—'}\n"
            f"{hl('Card:')}        {record.card.value}\n"
            f"{hl('Modifiers:')}   {modifier_str}\n"
            f"{hl('Model:')}       {model_label}\n"
            f"{hl('Temperature:')} {record.temperature:.2f}\n"
            f"{hl('Status:')}      {status_markup(record.status.value)}\n"
            f"{hl('Tags:')}        {tags_str}\n"
            f"{hl('Created:')}     {record.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"{hl('System prompt:')}\n{prompt_preview}",
            title=record.name,
            card=record.card,
        )
    )


@app.command("edit")
def edit(
    name: str = typer.Argument(..., help="Agent name or UUID"),
    new_name: str | None = typer.Option(None, "--name", "-n", help="New name"),
    description: str | None = typer.Option(None, "--description", "-d", help="Description"),
    card: str | None = typer.Option(None, "--card", "-c", help="Card id"),
    model: str | None = typer.Option(None, "--model", "-m", help="Connection name"),
    tags: str | None = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
) -> None:
    """Edit an agent's name, description, card, model, or tags."""
    record = _resolve_agent(name)

    updated_name = new_name if new_name is not None else typer.prompt("Name", default=record.name)
    updated_desc = (
        description if description is not None else typer.prompt("Description", default=record.description or "")
    )

    if card is not None:
        try:
            updated_card = _validate_card(card)
        except ValueError as exc:
            console.print(err(str(exc)))
            raise typer.Exit(1) from exc
        updated_modifiers = record.modifier_cards
    else:
        picked = select_card("Choose a card", initial=record.card)
        if picked is None:
            raise typer.Exit()
        updated_card = picked
        updated_modifiers = record.modifier_cards
        if typer.confirm("Edit modifier cards?", default=bool(record.modifier_cards)):
            raw_modifiers = select_cards(
                "Modifier cards (Space to toggle, Enter to confirm)",
                initial=record.modifier_cards,
                max_items=CardEngine.MAX_MODIFIERS,
                exclude={updated_card, Card.WORLD},
            )
            updated_modifiers = [m for m in raw_modifiers if m != updated_card]
            if updated_modifiers:
                _print_compat(
                    CardEngine(get_registry()).check_compatibility(updated_card, updated_modifiers),
                    get_registry(),
                )

    if model is not None:
        conn = _store().get_by_name(model)
        if conn is None:
            console.print(err(f"No connection named '{model}'."))
            raise typer.Exit(1)
        updated_conn_id = conn.id
    else:
        conn_map = {c.id: c for c in _store().all()}
        current_conn = conn_map.get(record.model_connection_id)
        updated_conn_id, _ = _pick_connection(default_name=current_conn.name if current_conn else None)

    if tags is not None:
        updated_tags = [t.strip() for t in tags.split(",") if t.strip()]
    else:
        tags_input = typer.prompt("Tags (comma-separated)", default=", ".join(record.tags))
        updated_tags = [t.strip() for t in tags_input.split(",") if t.strip()]

    updates: dict[str, Any] = {
        "name": updated_name,
        "description": updated_desc,
        "card": updated_card,
        "modifier_cards": updated_modifiers,
        "model_connection_id": updated_conn_id,
        "tags": updated_tags,
    }
    if updated_card != record.card or updated_modifiers != record.modifier_cards:
        config = CardEngine(get_registry()).resolve(updated_card, updated_modifiers)
        updates["temperature"] = config.temperature
        updates["system_prompt"] = config.system_prompt

    _registry().save(record.model_copy(update=updates))
    console.print(ok(f"Agent '{updated_name}' updated."))


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Agent name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an agent (soft-delete)."""
    record = _resolve_agent(name)
    if not yes:
        typer.confirm(f"Delete agent '{record.name}'?", abort=True)
    try:
        _registry().delete(record.id)
    except FileNotFoundError as e:
        console.print(err(f"Agent '{record.name}' was already deleted."))
        raise typer.Exit(1) from e
    console.print(ok(f"Agent '{record.name}' deleted."))
