from arcana.evals.types import EvalCase, EvalDimension, EvalResult, EvalRubric
from arcana.types.card import Card


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
) -> EvalResult:
    return EvalResult(
        case_id="test-case-001",
        run_id="test-run-001",
        card=card,
        model_id="ollama/hermes-3",
        response=response,
        error=error,
    )
