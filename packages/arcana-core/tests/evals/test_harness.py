"""Integration tests for EvalHarness — currently has zero test coverage."""

import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arcana.evals.harness import EvalHarness
from arcana.evals.suites.blending import BLENDING_CASES
from arcana.evals.suites.cards import CARD_CASES
from arcana.evals.types import EvalCase, EvalResult, EvalRubric, JudgeVerdict
from arcana.models.adapters.base import CompletionResponse, ModelChunk
from arcana.models.gateway import ModelGateway
from arcana.types.card import Card

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_gateway(content: str = "A thoughtful answer.") -> MagicMock:
    gw = MagicMock(spec=ModelGateway)
    gw.__aenter__ = AsyncMock(return_value=gw)
    gw.__aexit__ = AsyncMock(return_value=False)
    gw.complete = AsyncMock(return_value=CompletionResponse(content=content, input_tokens=10, output_tokens=5))

    async def _stream(_model: str, _req: object) -> AsyncGenerator[ModelChunk, None]:
        yield ModelChunk(text=content, input_tokens=10, output_tokens=5)

    gw.stream = _stream
    return gw


def _simple_case(
    case_id: str = "test-001",
    suite: str = "cards",
    card: Card = Card.HERMIT,
    required: list[str] | None = None,
    skip: bool = False,
) -> EvalCase:
    return EvalCase(
        id=case_id,
        description="Test case",
        suite=suite,
        card=card,
        prompt="What is RAG?",
        rubric=EvalRubric(required_elements=required or []),
        skip_reason="skipped for test" if skip else None,
    )


def _verdict(passed: bool = True, score: float = 0.9) -> JudgeVerdict:
    return JudgeVerdict(
        judge_type="rule",  # type: ignore
        dimension_scores=[],
        overall_score=score,
        passed=passed,
        reasoning="ok",
    )


# ---------------------------------------------------------------------------
# _load_cases()
# ---------------------------------------------------------------------------


def test_load_cases_returns_all_when_no_suite_filter():
    harness = EvalHarness(use_llm=False)
    cases = harness._load_cases(suite=None)
    assert len(cases) == len(CARD_CASES) + len(BLENDING_CASES)


def test_load_cases_filters_by_suite_cards():
    harness = EvalHarness(use_llm=False)
    cases = harness._load_cases(suite="cards")
    assert all(c.suite == "cards" for c in cases)
    assert len(cases) == len(CARD_CASES)


def test_load_cases_filters_by_suite_blending():
    harness = EvalHarness(use_llm=False)
    cases = harness._load_cases(suite="blending")
    assert all(c.suite == "blending" for c in cases)
    assert len(cases) == len(BLENDING_CASES)


def test_load_cases_unknown_suite_returns_empty():
    harness = EvalHarness(use_llm=False)
    cases = harness._load_cases(suite="nonexistent")
    assert cases == []


# ---------------------------------------------------------------------------
# _compare_to_baseline()
# ---------------------------------------------------------------------------


def test_compare_to_baseline_graceful_when_missing(tmp_path):
    harness = EvalHarness(use_llm=False, results_dir=tmp_path / "results")

    result = EvalResult(
        case_id="c-001",
        run_id="run-new",
        card=Card.HERMIT,
        model_id="ollama/test",
        response="answer",
        verdict=_verdict(),
    )

    report = harness._compare_to_baseline(
        run_id="run-new",
        baseline_run_id="run-does-not-exist",
        current_results=[result],
    )

    assert report.run_id == "run-new"
    assert report.baseline_run_id == "run-does-not-exist"
    assert report.cases_regressed == 0
    assert report.cases_improved == 0
    assert not report.has_regressions


def test_compare_to_baseline_detects_regression(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    baseline = [
        {
            "case_id": "c-001",
            "run_id": "run-old",
            "card": "the-hermit",
            "model_id": "ollama/test",
            "response": "old answer",
            "verdict": {
                "judge_type": "rule",
                "dimension_scores": [],
                "overall_score": 0.9,
                "passed": True,
                "reasoning": "was great",
            },
        }
    ]
    (results_dir / "run-old.json").write_text(json.dumps(baseline))

    harness = EvalHarness(use_llm=False, results_dir=results_dir)
    current_result = EvalResult(
        case_id="c-001",
        run_id="run-new",
        card=Card.HERMIT,
        model_id="ollama/test",
        response="new answer",
        verdict=_verdict(passed=False, score=0.5),
    )
    report = harness._compare_to_baseline(
        run_id="run-new",
        baseline_run_id="run-old",
        current_results=[current_result],
    )

    assert report.cases_regressed == 1
    assert report.has_regressions


def test_compare_to_baseline_detects_improvement(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True)
    baseline = [
        {
            "case_id": "c-001",
            "run_id": "run-old",
            "card": "the-hermit",
            "model_id": "ollama/test",
            "response": "mediocre answer",
            "verdict": {
                "judge_type": "rule",
                "dimension_scores": [],
                "overall_score": 0.5,
                "passed": False,
                "reasoning": "meh",
            },
        }
    ]
    (results_dir / "run-old.json").write_text(json.dumps(baseline))

    harness = EvalHarness(use_llm=False, results_dir=results_dir)
    current_result = EvalResult(
        case_id="c-001",
        run_id="run-new",
        card=Card.HERMIT,
        model_id="ollama/test",
        response="great answer",
        verdict=_verdict(passed=True, score=0.95),
    )
    report = harness._compare_to_baseline(
        run_id="run-new",
        baseline_run_id="run-old",
        current_results=[current_result],
    )

    assert report.cases_improved == 1
    assert not report.has_regressions


# ---------------------------------------------------------------------------
# run() — end-to-end with mock gateway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_summary_with_correct_counts(tmp_path):
    harness = EvalHarness(use_llm=False, default_model="ollama/hermes-3", results_dir=tmp_path / "results")
    cases = [
        _simple_case("c-001", required=["answer"]),
        _simple_case("c-002", required=[]),
    ]
    gw = _mock_gateway(content="answer to the question")

    with patch.object(harness, "_load_cases", return_value=cases):
        with patch("arcana.evals.harness.ModelGateway") as MockGW:
            with patch("arcana.evals.harness.ConnectionStore"):
                MockGW.return_value.__aenter__ = AsyncMock(return_value=gw)
                MockGW.return_value.__aexit__ = AsyncMock(return_value=False)
                summary = await harness.run()

    assert summary.cases_run == 2
    assert summary.cases_errored == 0


@pytest.mark.asyncio
async def test_run_saves_results_to_disk(tmp_path):
    results_dir = tmp_path / "results"
    harness = EvalHarness(use_llm=False, default_model="ollama/hermes-3", results_dir=results_dir)
    gw = _mock_gateway()

    with patch.object(harness, "_load_cases", return_value=[_simple_case("c-001")]):
        with patch("arcana.evals.harness.ModelGateway") as MockGW:
            with patch("arcana.evals.harness.ConnectionStore"):
                MockGW.return_value.__aenter__ = AsyncMock(return_value=gw)
                MockGW.return_value.__aexit__ = AsyncMock(return_value=False)
                summary = await harness.run()

    saved_files = list(results_dir.glob("*.json"))
    assert len(saved_files) == 1
    assert summary.run_id in saved_files[0].name


@pytest.mark.asyncio
async def test_run_skipped_case_is_counted(tmp_path):
    harness = EvalHarness(use_llm=False, default_model="ollama/hermes-3", results_dir=tmp_path / "results")
    gw = _mock_gateway()

    with patch.object(harness, "_load_cases", return_value=[_simple_case("c-001", skip=True)]):
        with patch("arcana.evals.harness.ModelGateway") as MockGW:
            with patch("arcana.evals.harness.ConnectionStore"):
                MockGW.return_value.__aenter__ = AsyncMock(return_value=gw)
                MockGW.return_value.__aexit__ = AsyncMock(return_value=False)
                summary = await harness.run()

    assert summary.cases_skipped == 1


@pytest.mark.asyncio
async def test_run_with_suite_filter_uses_only_that_suite(tmp_path):
    harness = EvalHarness(use_llm=False, default_model="ollama/hermes-3", results_dir=tmp_path / "results")
    gw = _mock_gateway()

    with patch("arcana.evals.harness.ModelGateway") as MockGW:
        with patch("arcana.evals.harness.ConnectionStore"):
            MockGW.return_value.__aenter__ = AsyncMock(return_value=gw)
            MockGW.return_value.__aexit__ = AsyncMock(return_value=False)
            summary = await harness.run(suite="cards")

    assert summary.suite == "cards"
    assert summary.cases_run == len(CARD_CASES)
