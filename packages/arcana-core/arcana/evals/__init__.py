"""
Arcana Evaluation Harness.

Usage:
    import asyncio
    from arcana.evals.harness import EvalHarness

    async def main():
        harness = EvalHarness(use_llm=False)  # rules-only for fast CI
        summary = await harness.run(suite="cards")
        print(f"Pass rate: {summary.pass_rate:.1%}")

    asyncio.run(main())

Suites:
    cards       — does the card system produce meaningfully different agents?
    memory      — does memory actually improve responses?
    decay       — do stale entries rank below fresh ones?
    blending    — does multi-card blending produce balanced output?

Judges:
    RuleJudge       — deterministic, free, always runs in CI
    LLMJudge        — qualitative scoring via Claude Opus
    CompositeJudge  — both combined (default)

CLI:
    arcana eval run
    arcana eval run --suite cards
    arcana eval run --fast
    arcana eval run --baseline run-20260501-120000
"""

from arcana.evals.harness import EvalHarness
from arcana.evals.judge import CompositeJudge, LLMJudge, RuleJudge
from arcana.evals.types import EvalCase, EvalDimension, EvalResult, EvalRubric

__all__ = [
    "EvalHarness",
    "EvalCase",
    "EvalDimension",
    "EvalRubric",
    "EvalResult",
    "RuleJudge",
    "LLMJudge",
    "CompositeJudge",
]
