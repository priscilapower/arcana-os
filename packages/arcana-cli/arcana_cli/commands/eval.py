"""Evaluation harness CLI commands."""

import asyncio
import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from arcana.evals.harness import EvalHarness
from arcana.evals.suites.blending import BLENDING_CASES
from arcana.evals.suites.cards import CARD_CASES
from arcana_cli.ui.theme import ACCENT, CHECK, CROSS, GREEN, RED, TXT3, err, make_table

app = typer.Typer(help="Run evaluation suites.")
console = Console()


@app.command("run")
def run(
    suite: str | None = typer.Option(None, "--suite", "-s", help="Suite to run: cards | blending | all"),
    fast: bool = typer.Option(False, "--fast", help="Rules-only mode — no LLM judge. Fast, free, good for CI."),
    baseline: str | None = typer.Option(None, "--baseline", "-b", help="Baseline run ID for regression comparison"),
    model: str = typer.Option(
        "ollama/hermes-3",
        "--model",
        "-m",
        help="Model to use for agents under evaluation",
    ),
) -> None:
    """Run evaluation suites against live agents."""

    async def _run() -> None:
        harness = EvalHarness(
            use_llm=not fast,
            default_model=model,
        )
        summary = await harness.run(
            suite=suite if suite != "all" else None,
            baseline_run_id=baseline,
        )

        if summary.regression_report and summary.regression_report.has_regressions:
            console.print(f"\n[bold {RED}]Regressions detected:[/]")
            for reg in summary.regression_report.regressions:
                console.print(
                    f"  [{TXT3}]{reg.case_id}:[/] {reg.baseline_score:.3f}"
                    f" → [{RED}]{reg.current_score:.3f}[/] ([{RED}]{reg.delta:+.3f}[/])"
                )
            raise typer.Exit(1)

    asyncio.run(_run())


@app.command("list")
def list_cases(
    suite: str | None = typer.Option(None, "--suite", "-s"),
) -> None:
    """List all available eval cases."""
    all_cases = [*CARD_CASES, *BLENDING_CASES]
    if suite:
        all_cases = [c for c in all_cases if c.suite == suite]

    table = make_table(f"Eval Cases {'— ' + suite if suite else ''}")
    table.add_column("ID", style=ACCENT)
    table.add_column("Suite", style=TXT3)
    table.add_column("Card")
    table.add_column("Baseline")
    table.add_column("Description")

    for case in all_cases:
        table.add_row(
            case.id,
            case.suite,
            case.card.value,
            case.baseline_card.value if case.baseline_card else "—",
            case.description[:60] + "…" if len(case.description) > 60 else case.description,
        )
    console.print(table)


@app.command("results")
def show_results(
    run_id: str = typer.Argument(..., help="Run ID to show"),
) -> None:
    """Show results from a previous eval run."""
    results_path = Path(__file__).parent.parent.parent.parent.parent / "evals" / "results" / f"{run_id}.json"
    if not results_path.exists():
        console.print(err(f"No results found for run: {run_id}"))
        raise typer.Exit(1)

    data: list[dict[str, Any]] = json.loads(results_path.read_text())
    table = make_table(f"Results — {run_id}")
    table.add_column("Case ID", style=ACCENT)
    table.add_column("Card")
    table.add_column("Score")
    table.add_column("Passed")
    table.add_column("Latency")

    for r in data:
        verdict: dict[str, Any] = r.get("verdict") or {}
        score: float = verdict.get("overall_score", 0)
        passed: bool = verdict.get("passed", False)
        table.add_row(
            r["case_id"],
            r["card"],
            f"{score:.3f}",
            f"[{GREEN}]{CHECK}[/]" if passed else f"[{RED}]{CROSS}[/]",
            f"{r.get('latency_ms', 0)}ms",
        )
    console.print(table)
