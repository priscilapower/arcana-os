"""arcana world — inspect and drive The World's task router.

``world route "<prompt>"`` is a dry run: it resolves *which agent would run this*
via :class:`WorldEngine`, prints (or emits as JSON) the resulting
:class:`RoutingDecision`, and starts no session. The decision is still written to
the routing audit, exactly as a real turn would record it.
"""

import typer
from rich.table import Table

from arcana.agents.registry import AgentRegistry
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana.types import RoutingDecision
from arcana.world import NoRouteAskUser
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_ERROR, truncate
from arcana_cli.command_impl import AGENT_METAVAR, command_impl
from arcana_cli.commands.run import build_world_engine, find_agent, resolve_reflex_classifier
from arcana_cli.constants import AGENTS_BASE, ARCANA_HOME
from arcana_cli.ui.renderer import Renderer, View, fail, lines, renderer_for
from arcana_cli.ui.theme import dim, err, make_table

app = typer.Typer(help="Inspect and drive The World's task router.")


def _agent_name(reg: AgentRegistry, decision: RoutingDecision) -> str:
    if decision.resolved_agent_id is None:
        return "—"
    record = reg.get(decision.resolved_agent_id)
    return record.name if record is not None else str(decision.resolved_agent_id)


def _decision_table(reg: AgentRegistry, decision: RoutingDecision) -> Table:
    table = make_table("World — routing decision")
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Task", truncate(decision.task_preview))
    table.add_row("Resolved agent", _agent_name(reg, decision))
    table.add_row("Layer", decision.layer.value)
    if decision.matched_rule_id is not None:
        table.add_row("Matched rule", str(decision.matched_rule_id))
    if decision.reflex_confidence is not None:
        flag = " (low confidence)" if decision.low_confidence else ""
        table.add_row("Reflex confidence", f"{decision.reflex_confidence:.2f}{flag}")
    if decision.reflex_reasoning:
        table.add_row("Reflex reasoning", truncate(decision.reflex_reasoning))
    table.add_row("Candidates", str(len(decision.candidate_pool)))
    if decision.spread_id is not None:
        table.add_row("Spread", str(decision.spread_id))
    table.add_row("Latency", f"{decision.latency_ms} ms")
    return table


def route_cmd(
    prompt: str = typer.Argument(..., help="The prompt to route (not executed)"),
    agent: str | None = typer.Option(
        None, "--agent", "-a", metavar=AGENT_METAVAR, help="Bypass routing and resolve to this agent (name or UUID)"
    ),
    json_: bool = typer.Option(False, "--json", help="Emit the decision as JSON"),
) -> None:
    """Resolve which agent would run a prompt, without running it (dry run)."""
    run_async(route_prompt(renderer_for(json_), prompt, agent=agent))


@command_impl("world route")
async def route_prompt(r: Renderer, prompt: str, *, agent: str | None) -> None:
    """Route ``prompt`` (to ``agent`` when given) and show the decision; no session is started.

    When The World can't pick an agent the command exits ``EXIT_ERROR``, and
    the ``--json`` document is the (unresolved) decision itself.
    """
    if not prompt.strip():
        fail(r, "Prompt cannot be empty.")

    reg = AgentRegistry(AGENTS_BASE)
    explicit = None
    if agent is not None:
        explicit = find_agent(r, agent, reg)
        if explicit is None:
            fail(r, f"No agent '{agent}'.")

    # A dry run resolves (and may run the reflex classifier) but starts no
    # session, so it emits no learning signal — sessions/signals are unwired.
    store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
    try:
        async with ModelGateway(connections=store) as gw:
            engine = build_world_engine(reg, reflex=resolve_reflex_classifier(gw))
            decision = await engine.route(prompt, explicit_agent=explicit)
    except NoRouteAskUser as exc:
        r.emit(
            View(
                err("The World couldn't pick an agent — name one with --agent <name>."),
                exc.decision.model_dump(mode="json"),
            )
        )
        raise typer.Exit(EXIT_ERROR) from exc

    r.emit(
        View(
            lines(_decision_table(reg, decision), dim("dry run — no session was started.")),
            decision.model_dump(mode="json"),
        )
    )


app.command(name="route")(route_cmd)
