"""
Evaluation harness types.

EvalCase    — a single reproducible evaluation scenario
EvalRubric  — defines what good looks like (dimensions + rules)
EvalResult  — the output of running a case
JudgeVerdict — scores from a judge
RegressionReport — comparison between two eval runs
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types.card import Card
from arcana.types.memory import MemoryEntry
from arcana.types.session import ToolCall

# ---------------------------------------------------------------------------
# Rubric
# ---------------------------------------------------------------------------


class EvalDimension(BaseModel):
    """
    A single axis of quality for a judge to score.

    Examples:
        EvalDimension("depth", "Explores nuance and tradeoffs thoroughly", weight=2.0)
        EvalDimension("tone", "Measured and precise, not rushed", weight=1.0)
        EvalDimension("memory_recall", "References pre-seeded memory accurately", weight=1.5)
    """

    name: str
    description: str
    weight: float = 1.0
    min_score: float | None = None  # hard floor — overall FAIL if any dim below this


class EvalRubric(BaseModel):
    """
    Defines what good output looks like for an EvalCase.
    Used by both LLMJudge (dimensions) and RuleJudge (elements).
    """

    dimensions: list[EvalDimension] = []
    required_elements: list[str] = []  # substrings that MUST appear in output
    forbidden_elements: list[str] = []  # substrings that must NOT appear
    pass_threshold: float = 0.7  # overall score must meet this to pass


# ---------------------------------------------------------------------------
# Eval case
# ---------------------------------------------------------------------------


class EvalCase(BaseModel):
    """
    A single evaluation scenario. Fully reproducible — same inputs every run.

    Suites:
        cards        — does the card system produce meaningfully different agents?
        memory       — does memory actually improve responses?
        decay        — do stale entries rank below fresh ones?
        blending     — does multi-card blending produce balanced output?
        coordination — multi-agent scenarios (Phase 2)
    """

    id: str  # "hermit-research-depth-001"
    description: str
    suite: str  # "cards" | "memory" | "decay" | "blending"

    # Agent configuration
    card: Card
    modifier_cards: list[Card] = []
    model_override: str | None = None  # force a specific model; None = use default

    # Input
    prompt: str
    memory_state: list[MemoryEntry] = []  # pre-seeded memory entries
    context: str | None = None  # extra system context

    # Rubric
    rubric: EvalRubric

    # Comparison — what are we measuring against?
    baseline_card: Card | None = None  # compare this card on the same prompt
    baseline_case_id: str | None = None  # compare against a specific other case

    # Metadata
    tags: list[str] = []
    skip_reason: str | None = None  # set to skip this case with a message

    @property
    def skip(self) -> bool:
        return self.skip_reason is not None


# ---------------------------------------------------------------------------
# Judge verdict
# ---------------------------------------------------------------------------


class JudgeType(StrEnum):
    LLM = "llm"
    RULE = "rule"
    COMPOSITE = "composite"


class DimensionScore(BaseModel):
    dimension: str
    score: float  # 0.0–1.0
    reasoning: str = ""
    passed_min: bool = True  # False if score < dimension.min_score


class JudgeVerdict(BaseModel):
    judge_type: JudgeType
    dimension_scores: list[DimensionScore]
    rule_scores: dict[str, bool] = {}  # required/forbidden element checks
    overall_score: float  # weighted average of dimension scores
    passed: bool
    reasoning: str = ""
    model_used: str | None = None  # which model judged (LLM judge only)
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Eval result
# ---------------------------------------------------------------------------


class EvalResult(BaseModel):
    """The output of running a single EvalCase."""

    id: UUID = Field(default_factory=uuid4)
    case_id: str
    run_id: str  # groups results from one full eval run
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Agent context
    card: Card
    modifier_cards: list[Card] = []
    model_id: str

    # Output
    response: str
    tool_calls_made: list[ToolCall] = []
    memories_written: list[MemoryEntry] = []
    memories_retrieved: list[MemoryEntry] = []
    tokens_used: int = 0
    latency_ms: int = 0
    error: str | None = None  # set if the run itself failed

    # Verdict
    verdict: JudgeVerdict | None = None

    # Baseline comparison (populated by harness if baseline_card set)
    baseline_card: Card | None = None
    baseline_score: float | None = None
    score_delta: float | None = None  # verdict.overall_score - baseline_score

    @property
    def passed(self) -> bool:
        return self.verdict.passed if self.verdict else False

    @property
    def overall_score(self) -> float:
        return self.verdict.overall_score if self.verdict else 0.0


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------


class RegressionDetail(BaseModel):
    case_id: str
    suite: str
    baseline_score: float
    current_score: float
    delta: float
    judge_reasoning: str


class RegressionReport(BaseModel):
    """Produced by EvalHarness when run in regression mode."""

    run_id: str
    baseline_run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    cases_run: int
    cases_passed: int
    cases_failed: int
    cases_regressed: int  # score dropped more than regression_threshold
    cases_improved: int

    regression_threshold: float = 0.05  # delta below this triggers a regression flag
    regressions: list[RegressionDetail] = []
    improvements: list[RegressionDetail] = []

    @property
    def has_regressions(self) -> bool:
        return self.cases_regressed > 0

    @property
    def pass_rate(self) -> float:
        if self.cases_run == 0:
            return 0.0
        return self.cases_passed / self.cases_run


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------


class EvalRunSummary(BaseModel):
    """Top-level summary of a full eval run."""

    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    suite: str | None = None  # None = all suites

    cases_run: int
    cases_passed: int
    cases_failed: int
    cases_skipped: int
    cases_errored: int  # agent itself threw an error

    pass_rate: float
    avg_score: float
    avg_latency_ms: float
    total_tokens_used: int
    estimated_cost_usd: float | None = None

    results: list[EvalResult] = []
    regression_report: RegressionReport | None = None
