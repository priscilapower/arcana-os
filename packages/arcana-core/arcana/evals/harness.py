"""
EvalHarness — the runner.

Usage:
    # Run all suites
    harness = EvalHarness()
    summary = await harness.run()

    # Run a specific suite
    summary = await harness.run(suite="cards")

    # Run in fast mode (rules only, no LLM judge)
    summary = await harness.run(use_llm=False)

    # Run with regression comparison
    summary = await harness.run(baseline_run_id="run-2026-05-01")

    # CLI
    arcana eval run
    arcana eval run --suite cards
    arcana eval run --fast          # rules only
    arcana eval run --baseline <id> # regression check
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from arcana.evals.judge import CompositeJudge, LLMJudge, RuleJudge
from arcana.evals.types import (
    EvalCase,
    EvalResult,
    EvalRunSummary,
    JudgeVerdict,
    RegressionDetail,
    RegressionReport,
)

RESULTS_DIR = Path.home() / ".arcana" / "evals" / "results"


class EvalHarness:
    """
    Runs EvalCases against live agents and scores results.

    Responsibilities:
        - Load cases from suites
        - Run each case (skipping marked cases)
        - Score with the configured judge
        - Store results to ~/.arcana/evals/results/
        - Optionally compare against a baseline run (regression mode)
        - Return a summary
    """

    def __init__(
        self,
        use_llm: bool = True,
        judge_model: str | None = None,
        default_model: str = "ollama/hermes-3",
        concurrency: int = 3,
    ) -> None:
        self._use_llm = use_llm
        self._default_model = default_model
        self._concurrency = concurrency
        self._judge = CompositeJudge(
            llm_judge=LLMJudge(model=judge_model) if use_llm else None,
            use_llm=use_llm,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        suite: str | None = None,
        baseline_run_id: str | None = None,
    ) -> EvalRunSummary:
        """Run all (or a subset of) eval cases."""
        run_id = f"run-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        cases = self._load_cases(suite)

        print(f"\n🌌 Arcana Eval Harness")
        print(f"   Run ID:  {run_id}")
        print(f"   Cases:   {len(cases)}")
        print(f"   Judge:   {'LLM + Rules' if self._use_llm else 'Rules only'}")
        print(f"   Suite:   {suite or 'all'}\n")

        results = await self._run_cases(cases, run_id)
        self._save_results(run_id, results)

        regression = None
        if baseline_run_id:
            regression = self._compare_to_baseline(
                run_id, baseline_run_id, results
            )

        summary = self._build_summary(run_id, suite, cases, results, regression)
        self._print_summary(summary)
        return summary

    # ------------------------------------------------------------------
    # Case loading
    # ------------------------------------------------------------------

    def _load_cases(self, suite: str | None = None) -> list[EvalCase]:
        from arcana.evals.suites.cards import CARD_CASES
        from arcana.evals.suites.blending import BLENDING_CASES

        all_cases = [*CARD_CASES, *BLENDING_CASES]
        if suite:
            all_cases = [c for c in all_cases if c.suite == suite]
        return all_cases

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    async def _run_cases(
        self, cases: list[EvalCase], run_id: str
    ) -> list[EvalResult]:
        sem = asyncio.Semaphore(self._concurrency)
        tasks = [self._run_one(case, run_id, sem) for case in cases]
        return await asyncio.gather(*tasks)

    async def _run_one(
        self,
        case: EvalCase,
        run_id: str,
        sem: asyncio.Semaphore,
    ) -> EvalResult:
        if case.skip:
            print(f"  ⏭  [{case.id}] Skipped: {case.skip_reason}")
            return EvalResult(
                case_id=case.id,
                run_id=run_id,
                card=case.card,
                model_id="skipped",
                response="",
                verdict=JudgeVerdict(
                    judge_type="rule",  # type: ignore
                    dimension_scores=[],
                    overall_score=0.0,
                    passed=False,
                    reasoning=f"Skipped: {case.skip_reason}",
                ),
            )

        async with sem:
            start = time.monotonic()
            try:
                result = await self._execute_case(case, run_id)
            except Exception as e:
                result = EvalResult(
                    case_id=case.id,
                    run_id=run_id,
                    card=case.card,
                    modifier_cards=case.modifier_cards,
                    model_id=case.model_override or self._default_model,
                    response="",
                    error=str(e),
                    latency_ms=int((time.monotonic() - start) * 1000),
                )

            # Score
            result.verdict = await self._judge.score(case, result)
            status = "✅" if result.passed else "❌"
            print(
                f"  {status} [{case.id}] "
                f"score={result.overall_score:.3f} "
                f"({result.latency_ms}ms)"
            )
            return result

    async def _execute_case(
        self, case: EvalCase, run_id: str
    ) -> EvalResult:
        """
        Run the agent for this eval case and collect outputs.

        TODO: Wire to real AgentRegistry in Epic 5.
        Currently uses Agent directly with the card config.
        """
        from arcana.agents.agent import Agent
        from arcana.cards.registry import get_registry
        from arcana.models.adapters.ollama import OllamaAdapter
        from arcana.models.adapters.anthropic import AnthropicAdapter

        start = time.monotonic()

        # Resolve model
        model_str = case.model_override or self._default_model
        if model_str.startswith("ollama/"):
            model = OllamaAdapter(model=model_str.split("/", 1)[1])
        elif model_str.startswith("claude"):
            model = AnthropicAdapter(model=model_str)
        else:
            model = OllamaAdapter(model=model_str)

        agent = Agent(
            name=f"eval-{case.card.value}",
            card=case.card,
            modifier_cards=case.modifier_cards,
            model=model,
        )

        # Seed memory if provided
        if case.memory_state:
            # TODO: Epic 5 — seed via AgentRegistry + MemoryFederation
            pass

        response = await agent.run(case.prompt, context=case.context)
        latency_ms = int((time.monotonic() - start) * 1000)

        return EvalResult(
            case_id=case.id,
            run_id=run_id,
            card=case.card,
            modifier_cards=case.modifier_cards,
            model_id=model_str,
            response=response,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_results(self, run_id: str, results: list[EvalResult]) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / f"{run_id}.json"
        data = [r.model_dump(mode="json") for r in results]
        path.write_text(json.dumps(data, indent=2, default=str))

    def _load_results(self, run_id: str) -> list[EvalResult]:
        path = RESULTS_DIR / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No results for run: {run_id}")
        data = json.loads(path.read_text())
        return [EvalResult(**r) for r in data]

    # ------------------------------------------------------------------
    # Regression
    # ------------------------------------------------------------------

    def _compare_to_baseline(
        self,
        run_id: str,
        baseline_run_id: str,
        current_results: list[EvalResult],
        threshold: float = 0.05,
    ) -> RegressionReport:
        try:
            baseline_results = self._load_results(baseline_run_id)
        except FileNotFoundError:
            print(f"  ⚠️  Baseline run not found: {baseline_run_id}")
            return RegressionReport(
                run_id=run_id,
                baseline_run_id=baseline_run_id,
                cases_run=0,
                cases_passed=0,
                cases_failed=0,
                cases_regressed=0,
                cases_improved=0,
            )

        baseline_map = {r.case_id: r for r in baseline_results}
        regressions = []
        improvements = []

        for result in current_results:
            baseline = baseline_map.get(result.case_id)
            if not baseline or not baseline.verdict or not result.verdict:
                continue

            delta = result.overall_score - baseline.overall_score
            detail = RegressionDetail(
                case_id=result.case_id,
                suite=result.case_id.split("-")[0],
                baseline_score=baseline.overall_score,
                current_score=result.overall_score,
                delta=delta,
                judge_reasoning=result.verdict.reasoning[:200],
            )

            if delta < -threshold:
                regressions.append(detail)
            elif delta > threshold:
                improvements.append(detail)

        return RegressionReport(
            run_id=run_id,
            baseline_run_id=baseline_run_id,
            cases_run=len(current_results),
            cases_passed=sum(1 for r in current_results if r.passed),
            cases_failed=sum(1 for r in current_results if not r.passed),
            cases_regressed=len(regressions),
            cases_improved=len(improvements),
            regression_threshold=threshold,
            regressions=regressions,
            improvements=improvements,
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        run_id: str,
        suite: str | None,
        cases: list[EvalCase],
        results: list[EvalResult],
        regression: RegressionReport | None,
    ) -> EvalRunSummary:
        scored = [r for r in results if r.verdict and not r.error]
        errored = [r for r in results if r.error]
        skipped = [r for r in results if r.model_id == "skipped"]
        passed = [r for r in scored if r.passed]

        avg_score = sum(r.overall_score for r in scored) / len(scored) if scored else 0
        avg_latency = sum(r.latency_ms for r in scored) / len(scored) if scored else 0
        total_tokens = sum(r.tokens_used for r in results)

        return EvalRunSummary(
            run_id=run_id,
            suite=suite,
            cases_run=len(cases),
            cases_passed=len(passed),
            cases_failed=len(scored) - len(passed),
            cases_skipped=len(skipped),
            cases_errored=len(errored),
            pass_rate=len(passed) / len(scored) if scored else 0,
            avg_score=round(avg_score, 3),
            avg_latency_ms=round(avg_latency),
            total_tokens_used=total_tokens,
            results=results,
            regression_report=regression,
        )

    def _print_summary(self, summary: EvalRunSummary) -> None:
        print(f"\n{'='*50}")
        print(f"  Run: {summary.run_id}")
        print(f"  Pass rate:  {summary.pass_rate:.1%} ({summary.cases_passed}/{summary.cases_run})")
        print(f"  Avg score:  {summary.avg_score:.3f}")
        print(f"  Avg latency: {summary.avg_latency_ms:.0f}ms")
        if summary.cases_errored:
            print(f"  ⚠️  Errors: {summary.cases_errored}")
        if summary.regression_report and summary.regression_report.has_regressions:
            n = summary.regression_report.cases_regressed
            print(f"  🔴 Regressions: {n}")
        print(f"{'='*50}\n")
