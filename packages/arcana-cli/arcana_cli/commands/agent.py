"""Agent management commands.

Every command body is a renderer-agnostic coroutine (``list_agents``,
``show_agent``, ``create_agent``, ``edit_agent``, ``delete_agent``): its result
is a :class:`~arcana_cli.ui.renderer.Presentable` (a Rich view and the ``--json``
document of the same data), its errors go through
:func:`~arcana_cli.ui.renderer.fail`, and every question it asks goes through
the :class:`~arcana_cli.ui.renderer.Renderer` it is handed and names the option
that answers it without a prompt. The Typer callbacks only pick the renderer
and run the coroutine.
"""

from typing import Any
from uuid import UUID

import typer

from arcana.agents.registry import AgentRegistry
from arcana.cards.engine import BlendCompatibility, CardEngine
from arcana.cards.registry import CardRegistry, get_registry
from arcana.models.connection_store import ConnectionStore
from arcana.types import ModelConnection
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana_cli._async import run_async
from arcana_cli.constants import AGENTS_BASE, CONNECTIONS_PATH, ROMAN
from arcana_cli.ui.card_picker import select_card, select_cards
from arcana_cli.ui.renderer import (
    Choice,
    Question,
    Renderer,
    View,
    confirm_or_cancel,
    fail,
    renderer_for,
    required,
)
from arcana_cli.ui.theme import (
    GREEN,
    TXT3,
    dim,
    hl,
    make_panel,
    make_panel_fit,
    make_table,
    ok,
    status_markup,
    warn,
)

app = typer.Typer(help="Manage agents.")

#: The option that answers the card picker (and the modifier-card question) without a terminal.
CARD_FLAG = "--card"
#: The option that answers the model picker without a prompt.
MODEL_FLAG = "--model"


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


def _resolve_agent(r: Renderer, name_or_id: str) -> AgentRecord:
    """Resolve a name or UUID string to an AgentRecord, with ambiguity detection."""
    reg = _registry()
    try:
        uid = UUID(name_or_id)
        record = reg.get(uid)
        if record is not None and not record.is_archived:
            return record
        fail(r, f"No agent with ID '{name_or_id}'.")
    except ValueError:
        pass
    matches = [a for a in reg.list() if a.name == name_or_id]
    if not matches:
        fail(r, f"No agent '{name_or_id}'.")
    if len(matches) > 1:
        fail(r, f"Ambiguous name '{name_or_id}'. Use one of these IDs:", *(f"  {a.id}" for a in matches))
    return matches[0]


def _agent_summary(record: AgentRecord) -> dict[str, Any]:
    """The ``--json`` shape of one agent in a list."""
    return {
        "id": str(record.id),
        "name": record.name,
        "card": record.card.value,
        "model": record.model or None,
        "status": record.status.value,
    }


def _agent_detail(record: AgentRecord) -> dict[str, Any]:
    """The ``--json`` shape of one agent in full."""
    return {
        **_agent_summary(record),
        "description": record.description or None,
        "modifier_cards": [c.value for c in record.modifier_cards],
        "temperature": record.temperature,
        "tags": list(record.tags),
        "tool_subscriptions": list(record.tool_subscriptions),
        "created_at": record.created_at.isoformat(),
        "system_prompt": record.system_prompt,
    }


def _model_ref_problem(answer: str) -> str | None:
    """A validator for a typed model reference or model ID: blank is allowed, whitespace inside it is not."""
    return "A model reference has no spaces." if any(c.isspace() for c in answer.strip()) else None


class _TypeAReference:
    """The model picker's last choice: type a model reference instead of picking a connection."""


TYPE_A_REFERENCE = _TypeAReference()


async def _pick_model(r: Renderer) -> str:
    """Pick a configured connection, then a model ID on it; returns a provider[:name][/model_id] string.

    The last choice types a whole model reference instead, for a model no
    connection points at. ``--model`` answers the picker without a prompt.
    """
    connections = _store().all()
    if not connections:
        fail(r, "No provider connections configured. Run: arcana providers add")

    choices: list[Choice[ModelConnection | _TypeAReference]] = [
        Choice(c, f"{c.name}  ({c.provider} · default model: {c.default_model or '(none)'})") for c in connections
    ]
    choices.append(Choice(TYPE_A_REFERENCE, "Other — type a model reference"))
    picked = await r.select(choices, initial=[connections[0]], title="Model connection", flag=MODEL_FLAG)
    if picked is None:
        raise typer.Exit()
    if isinstance(picked, _TypeAReference):
        return (
            await r.ask(
                Question(
                    "Model reference (provider[:name]/model_id)",
                    validator=lambda a: required(a) or _model_ref_problem(a),
                    flag=MODEL_FLAG,
                )
            )
        ).strip()

    model_id = (
        await r.ask(
            Question(
                "Model ID (blank to use connection default)",
                default=picked.default_model or "",
                validator=_model_ref_problem,
                flag=MODEL_FLAG,
            )
        )
    ).strip()
    if model_id:
        return f"{picked.provider}:{picked.name}/{model_id}"
    if picked.default_model:
        return f"{picked.provider}:{picked.name}"
    return str(picked.provider)


def _print_compat(r: Renderer, compat: BlendCompatibility, registry: CardRegistry) -> None:
    """Note tension/synergy warnings after modifier selection. Non-blocking."""
    if compat.has_tensions:
        r.note(warn(f"\n  ✦ CLASH — {len(compat.tensions)} tension(s) in this blend:"))
        for a, b in compat.tensions:
            r.note(warn(f"    ✗  {registry.get(a).name} ↔ {registry.get(b).name}"))
    if compat.has_synergies:
        r.note(dim(f"\n  ✦ SYNERGY — {len(compat.synergies)} synergy/ies in this blend:"))
        for a, b in compat.synergies:
            r.note(dim(f"    ✓  {registry.get(a).name} + {registry.get(b).name}"))
    if compat.has_tensions or compat.has_synergies:
        r.note("")


@app.command("create")
def create(
    name: str = typer.Option(None, "--name", "-n", help="Agent name"),
    card: str = typer.Option(None, "--card", "-c", help="Card id (e.g. 'hermit')"),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model reference (e.g. 'anthropic/claude-sonnet-4-6', 'anthropic:work/claude-sonnet-4-6', 'anthropic')",
    ),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Create a new agent. Interactive if no flags provided."""
    run_async(create_agent(renderer_for(json_), name=name, card=card, model=model))


async def create_agent(r: Renderer, *, name: str | None, card: str | None, model: str | None) -> None:
    """Create an agent, asking for whatever the flags left out."""
    agent_name = name or await r.ask(Question("Agent name", validator=required, flag="--name"))

    modifier_cards: list[Card] = []

    if not card:
        card_enum = await select_card("Choose a primary card for this agent", renderer=r, flag=CARD_FLAG)
        if card_enum is None:
            raise typer.Exit()
        if card_enum == Card.WORLD:
            fail(r, "The World is reserved and cannot be assigned.")
        if await r.confirm("Blend with modifier cards?", default=False, flag=CARD_FLAG):
            raw_modifiers = await select_cards(
                "Select modifier cards (Space to toggle, Enter to confirm)",
                initial=[],
                max_items=CardEngine.MAX_MODIFIERS,
                exclude={card_enum, Card.WORLD},
                renderer=r,
                flag=CARD_FLAG,
            )
            modifier_cards = [m for m in raw_modifiers if m != card_enum]
            if modifier_cards:
                _print_compat(
                    r,
                    CardEngine(get_registry()).check_compatibility(card_enum, modifier_cards),
                    get_registry(),
                )
    else:
        try:
            card_enum = _validate_card(card)
        except ValueError as exc:
            fail(r, str(exc))
        if card_enum == Card.WORLD:
            fail(r, "The World is reserved and cannot be assigned.")

    if model is not None:
        model_str = model.strip()
        if not model_str or " " in model_str:
            fail(r, "--model must be non-empty with no whitespace.")
    else:
        model_str = await _pick_model(r)

    registry = get_registry()
    record = _registry().create(
        name=agent_name,
        card=card_enum,
        model=model_str,
        modifier_cards=modifier_cards,
    )
    tarot = registry.get(card_enum)
    modifier_str = (
        f"  {hl('Modifiers:')} " + ", ".join(registry.get(m).name for m in modifier_cards) + "\n"
        if modifier_cards
        else ""
    )
    panel = make_panel_fit(
        f"[bold {GREEN}]Agent '{record.name}' created.[/]\n\n"
        f"  {hl('ID:')}    [{TXT3}]{record.id}[/]\n"
        f"  {hl('Card:')}  {ROMAN[tarot.number]} · {tarot.name} — {tarot.archetype.role}\n"
        + modifier_str
        + f"  {hl('Model:')} {record.model or '(unset)'}\n"
        f"  {hl('Temp:')}  {record.temperature:.2f}",
        title="New Agent",
        card=card_enum,
    )
    r.emit(View(panel, _agent_detail(record)))


@app.command("list")
def list_cmd(json_: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    """List all registered agents."""
    run_async(list_agents(renderer_for(json_)))


async def list_agents(r: Renderer) -> None:
    """Every live agent: a table, or an array of agent summaries."""
    records = _registry().list()
    summaries = [_agent_summary(a) for a in records]
    if not records:
        r.emit(View(dim("No agents yet. Run: arcana agent create"), summaries))
        return

    table = make_table("Agents")
    table.add_column("Name", style="bold")
    table.add_column("Card")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("ID", style=TXT3)
    for a in records:
        model_label = a.model if a.model else dim("(unset)")
        table.add_row(a.name, a.card.value, model_label, status_markup(a.status.value), str(a.id)[:8] + "…")
    r.emit(View(table, summaries))


@app.command("show")
def show(
    name: str = typer.Argument(..., help="Agent name or UUID"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show full config for an agent."""
    run_async(show_agent(renderer_for(json_), name))


async def show_agent(r: Renderer, name: str) -> None:
    """One agent's full config (the human view previews the system prompt; JSON carries all of it)."""
    record = _resolve_agent(r, name)
    model_label = record.model if record.model else "unset — re-assign with: arcana agent edit"

    modifier_str = ", ".join(c.value for c in record.modifier_cards) or "none"
    tags_str = ", ".join(record.tags) or "none"
    prompt_preview = record.system_prompt[:200] + ("…" if len(record.system_prompt) > 200 else "")

    r.emit(
        View(
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
            ),
            _agent_detail(record),
        )
    )


@app.command("edit")
def edit(
    name: str = typer.Argument(..., help="Agent name or UUID"),
    new_name: str | None = typer.Option(None, "--name", "-n", help="New name"),
    description: str | None = typer.Option(None, "--description", "-d", help="Description"),
    card: str | None = typer.Option(None, "--card", "-c", help="Card id"),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Model reference (e.g. 'anthropic/claude-sonnet-4-6')"
    ),
    tags: str | None = typer.Option(None, "--tags", "-t", help="Comma-separated tags"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Edit an agent's name, description, card, model, or tags."""
    run_async(
        edit_agent(
            renderer_for(json_),
            name,
            new_name=new_name,
            description=description,
            card=card,
            model=model,
            tags=tags,
        )
    )


async def edit_agent(
    r: Renderer,
    name: str,
    *,
    new_name: str | None,
    description: str | None,
    card: str | None,
    model: str | None,
    tags: str | None,
) -> None:
    """Edit an agent, asking for whatever the flags left out; each prompt defaults to the current value."""
    record = _resolve_agent(r, name)

    updated_name = (
        new_name
        if new_name is not None
        else await r.ask(Question("Name", default=record.name, validator=required, flag="--name"))
    )
    updated_desc = (
        description
        if description is not None
        else await r.ask(Question("Description", default=record.description or "", flag="--description"))
    )

    if card is not None:
        try:
            updated_card = _validate_card(card)
        except ValueError as exc:
            fail(r, str(exc))
        updated_modifiers = record.modifier_cards
    else:
        picked = await select_card("Choose a card", initial=record.card, renderer=r, flag=CARD_FLAG)
        if picked is None:
            raise typer.Exit()
        updated_card = picked
        updated_modifiers = record.modifier_cards
        if await r.confirm("Edit modifier cards?", default=bool(record.modifier_cards), flag=CARD_FLAG):
            raw_modifiers = await select_cards(
                "Modifier cards (Space to toggle, Enter to confirm)",
                initial=record.modifier_cards,
                max_items=CardEngine.MAX_MODIFIERS,
                exclude={updated_card, Card.WORLD},
                renderer=r,
                flag=CARD_FLAG,
            )
            updated_modifiers = [m for m in raw_modifiers if m != updated_card]
            if updated_modifiers:
                _print_compat(
                    r,
                    CardEngine(get_registry()).check_compatibility(updated_card, updated_modifiers),
                    get_registry(),
                )

    if model is not None:
        updated_model = model.strip()
        if not updated_model or " " in updated_model:
            fail(r, "--model must be non-empty with no whitespace.")
    else:
        updated_model = (
            await r.ask(
                Question(
                    "Model (provider[:name]/model_id)",
                    default=record.model or "",
                    validator=_model_ref_problem,
                    flag=MODEL_FLAG,
                )
            )
        ).strip()
        if not updated_model:
            updated_model = record.model or ""

    if tags is not None:
        updated_tags = [t.strip() for t in tags.split(",") if t.strip()]
    else:
        tags_input = await r.ask(Question("Tags (comma-separated)", default=", ".join(record.tags), flag="--tags"))
        updated_tags = [t.strip() for t in tags_input.split(",") if t.strip()]

    updates: dict[str, Any] = {
        "name": updated_name,
        "description": updated_desc,
        "card": updated_card,
        "modifier_cards": updated_modifiers,
        "model": updated_model,
        "tags": updated_tags,
    }
    if updated_card != record.card or updated_modifiers != record.modifier_cards:
        config = CardEngine(get_registry()).resolve(updated_card, updated_modifiers)
        updates["temperature"] = config.temperature
        updates["system_prompt"] = config.system_prompt

    saved = record.model_copy(update=updates)
    _registry().save(saved)
    r.emit(View(ok(f"Agent '{updated_name}' updated."), _agent_detail(saved)))


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Agent name or UUID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Delete an agent (soft-delete)."""
    run_async(delete_agent(renderer_for(json_), name, yes=yes))


async def delete_agent(r: Renderer, name: str, *, yes: bool) -> None:
    """Soft-delete an agent once confirmed (or with ``yes``)."""
    record = _resolve_agent(r, name)
    if not yes:
        await confirm_or_cancel(r, f"Delete agent '{record.name}'?")
    try:
        _registry().delete(record.id)
    except FileNotFoundError:
        fail(r, f"Agent '{record.name}' was already deleted.")
    r.emit(View(ok(f"Agent '{record.name}' deleted."), {"deleted": record.name, "id": str(record.id)}))
