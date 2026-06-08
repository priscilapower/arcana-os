"""
Tests for the evaluation harness.

These tests cover the harness infrastructure itself (types, judges, rubrics).
They do NOT make real LLM calls — use mocked responses throughout.
Real LLM eval runs are in arcana/evals/suites/ and marked @pytest.mark.llm_eval.
"""

import pytest

from arcana.evals.judge import CompositeJudge, RuleJudge
from arcana.evals.types import (
    EvalCase,
    EvalDimension,
    EvalResult,
    EvalRubric,
    EvalRunSummary,
    JudgeType,
    RegressionReport,
)
from arcana.types.card import Card

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_case(
    card: Card = Card.HERMIT,
    required: list[str] | None = None,
    forbidden: list[str] | None = None,
    dimensions: list[EvalDimension] | None = None,
    pass_threshold: float = 0.7,
    baseline_card: Card | None = None,
) -> EvalCase:
    return EvalCase(
        id="test-case-001",
        description="A test case",
        suite="cards",
        card=card,
        baseline_card=baseline_card,
        prompt="What are the tradeoffs between RAG and fine-tuning?",
        rubric=EvalRubric(
            dimensions=dimensions or [],
            required_elements=required or [],
            forbidden_elements=forbidden or [],
            pass_threshold=pass_threshold,
        ),
    )


def make_result(
    response: str = "A thoughtful response about RAG and fine-tuning tradeoffs.",
    card: Card = Card.HERMIT,
    error: str | None = None,
    score: float | None = None,
) -> EvalResult:
    result = EvalResult(
        case_id="test-case-001",
        run_id="test-run-001",
        card=card,
        model_id="ollama/hermes-3",
        response=response,
        error=error,
    )
    return result


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


def test_eval_case_defaults():
    case = make_case()
    assert case.id == "test-case-001"
    assert case.suite == "cards"
    assert case.skip is False
    assert case.modifier_cards == []


def test_eval_case_skip():
    case = EvalCase(
        id="skipped",
        description="Skipped case",
        suite="cards",
        card=Card.FOOL,
        prompt="test",
        rubric=EvalRubric(),
        skip_reason="Not implemented yet",
    )
    assert case.skip is True


def test_eval_result_passed_without_verdict():
    result = make_result()
    assert result.passed is False
    assert result.overall_score == 0.0


def test_eval_dimension_min_score():
    dim = EvalDimension(
        name="depth",
        description="Must be deep",
        weight=1.0,
        min_score=0.6,
    )
    assert dim.min_score == 0.6


def test_regression_report_has_regressions():
    from arcana.evals.types import RegressionDetail

    report = RegressionReport(
        run_id="run-new",
        baseline_run_id="run-old",
        cases_run=10,
        cases_passed=7,
        cases_failed=3,
        cases_regressed=2,
        cases_improved=1,
        regressions=[
            RegressionDetail(
                case_id="cards-hermit-001",
                suite="cards",
                baseline_score=0.8,
                current_score=0.65,
                delta=-0.15,
                judge_reasoning="Response was shallower",
            )
        ],
    )
    assert report.has_regressions is True
    assert report.pass_rate == 0.7


# ---------------------------------------------------------------------------
# RuleJudge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_judge_required_element_present():
    case = make_case(required=["tradeoff", "cost"])
    result = make_result(response="The main tradeoff is cost vs accuracy.")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.judge_type == JudgeType.RULE
    assert verdict.rule_scores.get("required:tradeoff") is True
    assert verdict.rule_scores.get("required:cost") is True


@pytest.mark.asyncio
async def test_rule_judge_required_element_missing():
    case = make_case(required=["latency"])
    result = make_result(response="RAG is cheaper than fine-tuning.")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.rule_scores.get("required:latency") is False
    assert verdict.overall_score < 1.0


@pytest.mark.asyncio
async def test_rule_judge_forbidden_element_absent():
    case = make_case(forbidden=["it depends"])
    result = make_result(response="Fine-tuning works better for domain-specific tasks.")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.rule_scores.get("forbidden:it depends") is True


@pytest.mark.asyncio
async def test_rule_judge_forbidden_element_present():
    case = make_case(forbidden=["it depends"])
    result = make_result(response="Well, it depends on your use case really.")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.rule_scores.get("forbidden:it depends") is False
    assert verdict.passed is False


@pytest.mark.asyncio
async def test_rule_judge_all_pass():
    case = make_case(
        required=["rag", "fine-tuning"],
        forbidden=["it depends"],
        pass_threshold=0.8,
    )
    result = make_result(response="RAG retrieves context at runtime. Fine-tuning bakes it into weights.")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.overall_score == 1.0
    assert verdict.passed is True


@pytest.mark.asyncio
async def test_rule_judge_error_response():
    case = make_case()
    result = make_result(error="Model connection failed")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.passed is False
    assert verdict.overall_score == 0.0
    assert "error" in verdict.reasoning.lower()


@pytest.mark.asyncio
async def test_rule_judge_no_rules_passes_trivially():
    case = make_case()  # no required or forbidden
    result = make_result()
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.overall_score == 1.0
    assert verdict.passed is True


@pytest.mark.asyncio
async def test_rule_judge_case_insensitive():
    case = make_case(required=["RAG"])
    result = make_result(response="rag is useful for knowledge-intensive tasks.")
    judge = RuleJudge()
    verdict = await judge.score(case, result)

    assert verdict.rule_scores.get("required:RAG") is True


# ---------------------------------------------------------------------------
# CompositeJudge (rules-only mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composite_judge_rules_only_mode():
    """In fast mode (use_llm=False), composite behaves like rule judge."""
    case = make_case(required=["tradeoff"])
    result = make_result(response="The key tradeoff is between cost and quality.")
    judge = CompositeJudge(use_llm=False)
    verdict = await judge.score(case, result)

    assert verdict.rule_scores.get("required:tradeoff") is True
    assert verdict.passed is True


@pytest.mark.asyncio
async def test_composite_judge_no_dimensions_passes():
    """If no qualitative dimensions, falls through to rules only."""
    case = make_case(required=["cost"])
    result = make_result(response="Cost is the main factor here.")
    judge = CompositeJudge(use_llm=False)
    verdict = await judge.score(case, result)
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# EvalRunSummary
# ---------------------------------------------------------------------------


def test_run_summary_pass_rate():
    summary = EvalRunSummary(
        run_id="test",
        cases_run=10,
        cases_passed=7,
        cases_failed=3,
        cases_skipped=0,
        cases_errored=0,
        pass_rate=0.7,
        avg_score=0.75,
        avg_latency_ms=1200.0,
        total_tokens_used=50000,
    )
    assert summary.pass_rate == 0.7
    assert summary.cases_run == 10


# ---------------------------------------------------------------------------
# Suite definitions
# ---------------------------------------------------------------------------


def test_card_suite_cases_are_valid():
    from arcana.evals.suites.cards import CARD_CASES

    assert len(CARD_CASES) > 0
    for case in CARD_CASES:
        assert case.suite == "cards"
        assert case.id.startswith("cards-")
        assert case.prompt
        assert case.rubric


def test_blending_suite_cases_have_modifiers():
    from arcana.evals.suites.blending import BLENDING_CASES

    assert len(BLENDING_CASES) > 0
    for case in BLENDING_CASES:
        assert case.suite == "blending"
        assert len(case.modifier_cards) > 0


def test_all_case_ids_are_unique():
    from arcana.evals.suites.blending import BLENDING_CASES
    from arcana.evals.suites.cards import CARD_CASES

    all_cases = [*CARD_CASES, *BLENDING_CASES]
    ids = [c.id for c in all_cases]
    assert len(ids) == len(set(ids)), "Duplicate eval case IDs found"
