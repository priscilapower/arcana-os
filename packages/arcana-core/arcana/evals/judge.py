"""
Judges — score EvalCase outputs against rubrics.

RuleJudge      — deterministic checks; free, always runs in CI
LLMJudge       — uses a model to score qualitative dimensions
CompositeJudge — combines both; rule checks always run

Design principle:
  Never use the same model as both the agent under evaluation and the judge.
  Default judge model: Claude Opus (strongest available).
  This prevents circular evaluation where the model grades its own outputs.
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Any

try:
    import anthropic
    from anthropic.types import TextBlock
except ImportError as e:
    raise ImportError("Install arcana-core[anthropic] to use LLMJudge") from e

from arcana.evals.types import (
    DimensionScore,
    EvalCase,
    EvalResult,
    JudgeType,
    JudgeVerdict,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseJudge(ABC):
    @abstractmethod
    async def score(self, case: EvalCase, result: EvalResult) -> JudgeVerdict: ...


# ---------------------------------------------------------------------------
# Rule judge — deterministic, no LLM
# ---------------------------------------------------------------------------


class RuleJudge(BaseJudge):
    """
    Deterministic checks against EvalRubric.required_elements and
    EvalRubric.forbidden_elements. Fast, cheap, always runs.

    Scores:
        required: 1.0 if present, 0.0 if absent
        forbidden: 1.0 if absent, 0.0 if present
        overall: fraction of rules passed
    """

    async def score(self, case: EvalCase, result: EvalResult) -> JudgeVerdict:
        if result.error:
            return self._error_verdict(result.error)

        response_lower = result.response.lower()
        rule_scores: dict[str, bool] = {}
        dimension_scores: list[DimensionScore] = []

        # Required elements
        for element in case.rubric.required_elements:
            present = element.lower() in response_lower
            key = f"required:{element}"
            rule_scores[key] = present
            dimension_scores.append(
                DimensionScore(
                    dimension=key,
                    score=1.0 if present else 0.0,
                    reasoning=f"'{element}' {'found' if present else 'NOT FOUND'} in response",
                )
            )

        # Forbidden elements
        for element in case.rubric.forbidden_elements:
            absent = element.lower() not in response_lower
            key = f"forbidden:{element}"
            rule_scores[key] = absent
            dimension_scores.append(
                DimensionScore(
                    dimension=key,
                    score=1.0 if absent else 0.0,
                    reasoning=f"'{element}' {'correctly absent' if absent else 'FOUND — should not be present'}",
                )
            )

        total = len(dimension_scores)
        overall = sum(d.score for d in dimension_scores) / total if total > 0 else 1.0
        passed = overall >= case.rubric.pass_threshold

        return JudgeVerdict(
            judge_type=JudgeType.RULE,
            dimension_scores=dimension_scores,
            rule_scores=rule_scores,
            overall_score=round(overall, 3),
            passed=passed,
            reasoning=f"{sum(rule_scores.values())}/{total} rules passed",
        )

    def _error_verdict(self, error: str) -> JudgeVerdict:
        return JudgeVerdict(
            judge_type=JudgeType.RULE,
            dimension_scores=[],
            overall_score=0.0,
            passed=False,
            reasoning=f"Agent error: {error}",
        )


# ---------------------------------------------------------------------------
# LLM judge — qualitative scoring
# ---------------------------------------------------------------------------


class LLMJudge(BaseJudge):
    """
    Uses a strong LLM to score qualitative dimensions against the rubric.

    Always uses a different model than the agent being evaluated.
    Default: claude-opus-4 (strongest available, most reliable judge).

    Prompt strategy:
        - Provides the original prompt, the response, and each dimension
        - Asks for a score (0.0–1.0) and one-sentence reasoning per dimension
        - Requests JSON output for reliable parsing
        - Instructs the judge to be calibrated, not generous
    """

    JUDGE_MODEL = "claude-opus-4-5"

    JUDGE_SYSTEM = """You are a calibrated evaluator of AI agent responses.
    Your job is to score responses against defined quality dimensions.

    Scoring guidelines:
      1.0 = Excellent — clearly meets the dimension's criteria
      0.7 = Good — mostly meets criteria with minor gaps
      0.5 = Partial — meets some criteria but has notable gaps
      0.3 = Poor — barely meets criteria
      0.0 = Fail — does not meet the dimension's criteria at all

    Be calibrated, not generous. A score of 0.7 means "good but not excellent".
    Respond ONLY with valid JSON matching the specified schema. No preamble.
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model or self.JUDGE_MODEL

    async def score(self, case: EvalCase, result: EvalResult) -> JudgeVerdict:
        if result.error:
            return JudgeVerdict(
                judge_type=JudgeType.LLM,
                dimension_scores=[],
                overall_score=0.0,
                passed=False,
                reasoning=f"Agent error: {result.error}",
                model_used=self._model,
            )

        if not case.rubric.dimensions:
            return JudgeVerdict(
                judge_type=JudgeType.LLM,
                dimension_scores=[],
                overall_score=1.0,
                passed=True,
                reasoning="No qualitative dimensions defined — trivially passed",
                model_used=self._model,
            )

        start = time.monotonic()
        raw_scores = await self._call_judge(case, result)
        latency_ms = int((time.monotonic() - start) * 1000)

        dimension_scores = self._parse_scores(raw_scores, case)
        overall = self._weighted_average(dimension_scores, case)

        # Hard floor check
        any_failed_floor = any(d.passed_min is False for d in dimension_scores)
        passed = overall >= case.rubric.pass_threshold and not any_failed_floor

        reasoning_parts = [f"{d.dimension}: {d.score:.2f} — {d.reasoning}" for d in dimension_scores]

        return JudgeVerdict(
            judge_type=JudgeType.LLM,
            dimension_scores=dimension_scores,
            overall_score=round(overall, 3),
            passed=passed,
            reasoning="\n".join(reasoning_parts),
            model_used=self._model,
            latency_ms=latency_ms,
        )

    async def _call_judge(self, case: EvalCase, result: EvalResult) -> dict[str, Any]:
        """Call the judge model. Returns raw parsed JSON."""
        dimensions_spec = "\n".join(f'  - "{d.name}": {d.description}' for d in case.rubric.dimensions)
        schema = {
            "scores": {
                d.name: {"score": "float 0.0-1.0", "reasoning": "one sentence"} for d in case.rubric.dimensions
            },
            "overall_reasoning": "one paragraph summary",
        }

        user_message = f"""Evaluate this AI agent response.

        ## Original prompt
        {case.prompt}

        ## Agent response
        {result.response}

        ## Dimensions to score
        {dimensions_spec}

        ## Required JSON schema
        {json.dumps(schema, indent=2)}

        Score each dimension from 0.0 to 1.0 with one-sentence reasoning."""

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=self._model,
            max_tokens=1000,
            system=self.JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )

        first_block = response.content[0]
        raw = first_block.text.strip() if isinstance(first_block, TextBlock) else ""
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    def _parse_scores(self, raw: dict[str, Any], case: EvalCase) -> list[DimensionScore]:
        scores_data = raw.get("scores", {})
        result: list[DimensionScore] = []
        for dim in case.rubric.dimensions:
            raw_dim = scores_data.get(dim.name, {})
            score = float(raw_dim.get("score", 0.5))
            reasoning = raw_dim.get("reasoning", "")
            passed_min = True
            if dim.min_score is not None and score < dim.min_score:
                passed_min = False
            result.append(
                DimensionScore(
                    dimension=dim.name,
                    score=round(score, 3),
                    reasoning=reasoning,
                    passed_min=passed_min,
                )
            )
        return result

    def _weighted_average(self, scores: list[DimensionScore], case: EvalCase) -> float:
        dim_map = {d.name: d for d in case.rubric.dimensions}
        total_weight = sum(dim_map[s.dimension].weight for s in scores if s.dimension in dim_map)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(s.score * dim_map[s.dimension].weight for s in scores if s.dimension in dim_map)
        return weighted_sum / total_weight


# ---------------------------------------------------------------------------
# Composite judge — rules always run; LLM optional
# ---------------------------------------------------------------------------


class CompositeJudge(BaseJudge):
    """
    Combines RuleJudge and LLMJudge.

    Rule checks are always free and fast — run them regardless.
    LLM scoring is optional (set use_llm=False for CI fast path).

    Scoring:
        If use_llm=True:  overall = llm_weight * llm_score + (1-llm_weight) * rule_score
        If use_llm=False: overall = rule_score only
    """

    def __init__(
        self,
        llm_judge: LLMJudge | None = None,
        use_llm: bool = True,
        llm_weight: float = 0.7,
    ) -> None:
        self._rule = RuleJudge()
        self._llm = llm_judge or LLMJudge()
        self._use_llm = use_llm
        self._llm_weight = llm_weight

    async def score(self, case: EvalCase, result: EvalResult) -> JudgeVerdict:
        rule_verdict = await self._rule.score(case, result)

        if not self._use_llm or not case.rubric.dimensions:
            return rule_verdict

        llm_verdict = await self._llm.score(case, result)

        # Combine scores
        rule_w = 1.0 - self._llm_weight
        overall = self._llm_weight * llm_verdict.overall_score + rule_w * rule_verdict.overall_score

        # Merge dimension scores
        all_dimensions = llm_verdict.dimension_scores + rule_verdict.dimension_scores
        any_failed_floor = any(not d.passed_min for d in all_dimensions)
        passed = (
            overall >= case.rubric.pass_threshold
            and not any_failed_floor
            and rule_verdict.passed  # all required/forbidden must pass
        )

        return JudgeVerdict(
            judge_type=JudgeType.COMPOSITE,
            dimension_scores=all_dimensions,
            rule_scores=rule_verdict.rule_scores,
            overall_score=round(overall, 3),
            passed=passed,
            reasoning=(
                f"LLM ({self._llm_weight:.0%}): {llm_verdict.overall_score:.3f}\n"
                f"Rules ({rule_w:.0%}): {rule_verdict.overall_score:.3f}\n"
                f"Combined: {overall:.3f}\n\n"
                f"{llm_verdict.reasoning}"
            ),
            model_used=llm_verdict.model_used,
            latency_ms=llm_verdict.latency_ms,
        )
