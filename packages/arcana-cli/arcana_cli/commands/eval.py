"""Evaluation harness CLI commands."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Run evaluation suites.")
console = Console()


@app.command("run")
def run(
    suite: str | None = typer.Option(
        None, "--suite", "-s",
        help="Suite to run: cards | memory | decay | blending | all"
    ),
    fast: bool = typer.Option(
        False, "--fast",
        help="Rules-only mode — no LLM judge. Fast, free, good for CI."
    ),
    baseline: str | None = typer.Option(
        None, "--baseline", "-b",
        help="Baseline run ID for regression comparison"
    ),
    model: str = typer.Option(
        "ollama/hermes-3", "--model", "-m",
        help="Model to use for agents under evaluation"
    ),
) -> None:
    """Run evaluation suites against live agents."""
    from arcana.evals.harness import EvalHarness

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
            console.print("\n[red]🔴 Regressions detected:[/red]")
            for reg in summary.regression_report.regressions:
                console.print(
                    f"  {reg.case_id}: "
                    f"{reg.baseline_score:.3f} → {reg.current_score:.3f} "
                    f"({reg.delta:+.3f})"
                )
            raise typer.Exit(1)

    asyncio.run(_run())


@app.command("list")
def list_cases(
    suite: str | None = typer.Option(None, "--suite", "-s"),
) -> None:
    """List all available eval cases."""
    from arcana.evals.suites.cards import CARD_CASES
    from arcana.evals.suites.memory import MEMORY_CASES
    from arcana.evals.suites.decay import DECAY_CASES
    from arcana.evals.suites.blending import BLENDING_CASES

    all_cases = [*CARD_CASES, *MEMORY_CASES, *DECAY_CASES, *BLENDING_CASES]
    if suite:
        all_cases = [c for c in all_cases if c.suite == suite]

    table = Table(title=f"Eval Cases {'— ' + suite if suite else ''}")
    table.add_column("ID", style="cyan")
    table.add_column("Suite", style="dim")
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
    import json
    from pathlib import Path

    results_path = Path(__file__).parent.parent.parent.parent.parent / "evals" / "results" / f"{run_id}.json"
    if not results_path.exists():
        console.print(f"[red]No results found for run: {run_id}[/red]")
        raise typer.Exit(1)

    data = json.loads(results_path.read_text())
    table = Table(title=f"Results — {run_id}")
    table.add_column("Case ID", style="cyan")
    table.add_column("Card")
    table.add_column("Score")
    table.add_column("Passed")
    table.add_column("Latency")

    for r in data:
        verdict = r.get("verdict") or {}
        score = verdict.get("overall_score", 0)
        passed = verdict.get("passed", False)
        table.add_row(
            r["case_id"],
            r["card"],
            f"{score:.3f}",
            "✅" if passed else "❌",
            f"{r.get('latency_ms', 0)}ms",
        )
    console.print(table)
