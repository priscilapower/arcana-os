import pytest

from arcana.evals.judge import CompositeJudge, RuleJudge
from arcana.evals.types import JudgeType

from .conftest import make_case, make_result

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
