"""arcana world — inspect and drive The World's task router.

``world route "<prompt>"`` is a dry run: it resolves *which agent would run this*
via :class:`WorldEngine`, prints (or emits as JSON) the resulting
:class:`RoutingDecision`, and starts no session. The decision is still written to
the routing audit, exactly as a real turn would record it.
"""

import typer
from rich.console import Console

from arcana.agents.registry import AgentRegistry
from arcana.types import RoutingDecision
from arcana.world import NoRouteAskUser
from arcana_cli._render import EXIT_ERROR, emit_json, truncate
from arcana_cli.commands.run import build_world_engine, find_agent
from arcana_cli.constants import AGENTS_BASE
from arcana_cli.ui.theme import dim, err, make_table

app = typer.Typer(help="Inspect and drive The World's task router (route).")
console = Console()


def _agent_name(reg: AgentRegistry, decision: RoutingDecision) -> str:
    if decision.resolved_agent_id is None:
        return "—"
    record = reg.get(decision.resolved_agent_id)
    return record.name if record is not None else str(decision.resolved_agent_id)


def _print_decision(reg: AgentRegistry, decision: RoutingDecision) -> None:
    table = make_table("World — routing decision")
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Task", truncate(decision.task_preview))
    table.add_row("Resolved agent", _agent_name(reg, decision))
    table.add_row("Layer", decision.layer.value)
    if decision.matched_rule_id is not None:
        table.add_row("Matched rule", str(decision.matched_rule_id))
    table.add_row("Candidates", str(len(decision.candidate_pool)))
    if decision.spread_id is not None:
        table.add_row("Spread", str(decision.spread_id))
    table.add_row("Latency", f"{decision.latency_ms} ms")
    console.print(table)


def route_cmd(
    prompt: str = typer.Argument(..., help="The prompt to route (not executed)"),
    agent: str | None = typer.Option(
        None, "--agent", "-a", help="Bypass routing and resolve to this agent (name or UUID)"
    ),
    json_: bool = typer.Option(False, "--json", help="Emit the decision as JSON"),
) -> None:
    """Resolve which agent would run a prompt, without running it (dry run)."""
    if not prompt.strip():
        console.print(err("Prompt cannot be empty."))
        raise typer.Exit(EXIT_ERROR)

    reg = AgentRegistry(AGENTS_BASE)
    explicit = None
    if agent is not None:
        explicit = find_agent(agent, reg)
        if explicit is None:
            console.print(err(f"No agent '{agent}'."))
            raise typer.Exit(EXIT_ERROR)

    engine = build_world_engine(reg)
    try:
        decision = engine.route(prompt, explicit_agent=explicit)
    except NoRouteAskUser as exc:
        if json_:
            emit_json(exc.decision.model_dump(mode="json"))
        else:
            console.print(err("The World couldn't pick an agent — name one with --agent <name>."))
        raise typer.Exit(EXIT_ERROR) from exc

    if json_:
        emit_json(decision.model_dump(mode="json"))
    else:
        _print_decision(reg, decision)
        console.print(dim("dry run — no session was started."))


app.command(name="route")(route_cmd)
