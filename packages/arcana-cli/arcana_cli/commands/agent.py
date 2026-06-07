"""Agent management commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from arcana.types.card import Card

app = typer.Typer(help="Manage agents.")
console = Console()

CARD_TABLE = {
    Card.FOOL: ("0", "The Fool", "Explorer / Autonomous Agent", "0.95"),
    Card.MAGICIAN: ("I", "The Magician", "Executor / Tool Master", "0.50"),
    Card.HIGH_PRIESTESS: (
        "II",
        "The High Priestess",
        "Archivist / Pattern Reader",
        "0.40",
    ),
    Card.EMPRESS: ("III", "The Empress", "Creator / Generative Agent", "0.85"),
    Card.EMPEROR: ("IV", "The Emperor", "Orchestrator / System Agent", "0.30"),
    Card.HIEROPHANT: ("V", "The Hierophant", "Advisor / Domain Expert", "0.30"),
    Card.LOVERS: ("VI", "The Lovers", "Collaborator / Communication", "0.70"),
    Card.CHARIOT: ("VII", "The Chariot", "Driver / Goal Agent", "0.40"),
    Card.STRENGTH: ("VIII", "Strength", "Coach / Long-Game Agent", "0.60"),
    Card.HERMIT: ("IX", "The Hermit", "Researcher / Deep Analyst", "0.35"),
    Card.WHEEL_OF_FORTUNE: (
        "X",
        "Wheel of Fortune",
        "Scheduler / Probabilistic",
        "0.65",
    ),
    Card.JUSTICE: ("XI", "Justice", "Auditor / Evaluation Agent", "0.20"),
    Card.HANGED_MAN: ("XII", "The Hanged Man", "Reframer / Perspective", "0.80"),
    Card.DEATH: ("XIII", "Death", "Transformer / Refactor Agent", "0.40"),
    Card.TEMPERANCE: ("XIV", "Temperance", "Integrator / Synthesis", "0.55"),
    Card.DEVIL: ("XV", "The Devil", "Shadow / Constraint Breaker", "0.75"),
    Card.TOWER: ("XVI", "The Tower", "Disruptor / Breakthrough", "0.85"),
    Card.STAR: ("XVII", "The Star", "Companion / Wellbeing Agent", "0.70"),
    Card.MOON: ("XVIII", "The Moon", "Interpreter / Ambiguity", "0.80"),
    Card.SUN: ("XIX", "The Sun", "Amplifier / Output Agent", "0.75"),
    Card.JUDGEMENT: ("XX", "Judgement", "Reviewer / Reflection", "0.45"),
    Card.WORLD: ("XXI", "The World", "Meta-Agent [reserved]", "0.50"),
}


def _print_card_table() -> None:
    table = Table(title="🃏 The 22 Major Arcana", show_header=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Card", style="bold")
    table.add_column("Archetype")
    table.add_column("Temp", width=5)
    table.add_column("Key", style="dim")

    for card, (num, name, archetype, temp) in CARD_TABLE.items():
        if card == Card.WORLD:
            continue  # The World is reserved
        table.add_row(num, name, archetype, temp, card.value)
    console.print(table)


@app.command("create")
def create(
    name: str = typer.Option(None, "--name", "-n", help="Agent name"),
    card: str = typer.Option(None, "--card", "-c", help="Card id (e.g. 'hermit')"),
    model: str = typer.Option(None, "--model", "-m", help="Model (e.g. 'ollama/hermes-3')"),
) -> None:
    """Create a new agent. Interactive if no flags provided."""
    if not name:
        name = typer.prompt("Agent name")

    if not card:
        _print_card_table()
        card = typer.prompt("\nChoose a card (enter the key, e.g. 'the-hermit')")

    # Validate card
    try:
        card_enum = Card(card) if "-" in card else Card(f"the-{card}")
    except ValueError as exception:
        # Try partial match
        matches = [c for c in Card if card.lower() in c.value]
        if len(matches) == 1:
            card_enum = matches[0]
        else:
            console.print(f"[red]Unknown card: {card}[/red]")
            raise typer.Exit(1) from exception

    if not model:
        model = typer.prompt("Model", default="ollama/hermes-3")

    card_info = CARD_TABLE.get(card_enum, ("?", card_enum.value, "", ""))
    console.print(
        f"\n[bold green]✨ Creating agent '{name}'[/bold green]\n"
        f"  Card:  {card_info[0]} · {card_info[1]} — {card_info[2]}\n"
        f"  Model: {model}\n"
        f"  Temp:  {card_info[3]}\n"
    )
    # TODO: persist via AgentRegistry in Epic 5
    console.print("[yellow]Agent persistence is implemented in Epic 5.[/yellow]")


@app.command("list")
def list_agents() -> None:
    """List all agents."""
    from pathlib import Path

    agents_dir = Path.home() / ".arcana" / "agents"
    if not agents_dir.exists() or not list(agents_dir.iterdir()):
        console.print("[dim]No agents yet. Run: arcana agent create[/dim]")
        return
    # TODO: load from registry in Epic 5
    console.print("[dim]Agent listing is implemented in Epic 5.[/dim]")


@app.command("show")
def show(name: str = typer.Argument(..., help="Agent name")) -> None:
    """Show full config for an agent."""
    # TODO: implement in Epic 5
    console.print(f"[dim]Showing agent '{name}' — implemented in Epic 5.[/dim]")


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Agent name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an agent."""
    if not yes:
        typer.confirm(f"Delete agent '{name}'?", abort=True)
    # TODO: implement in Epic 5
    console.print(f"[dim]Deleting agent '{name}' — implemented in Epic 5.[/dim]")
