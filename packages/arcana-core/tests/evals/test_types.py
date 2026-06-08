from arcana.evals.types import (
    EvalCase,
    EvalDimension,
    EvalRubric,
    EvalRunSummary,
    RegressionReport,
)
from arcana.types.card import Card

from .conftest import make_case, make_result


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
