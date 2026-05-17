"""
Memory quality eval suite.

Core question: does memory actually improve agent responses?
Does having relevant context produce measurably better answers?
Does confidence filtering block low-quality entries from surfacing?
"""

from arcana.evals.fixtures.memory_states import (
    fresh_work_context,
    low_confidence_state,
    rich_project_context,
)
from arcana.evals.fixtures.prompts import MEMORY_PROMPTS
from arcana.evals.types import EvalCase, EvalDimension, EvalRubric
from arcana.types.card import Card

MEMORY_CASES: list[EvalCase] = [

    EvalCase(
        id="memory-context-improves-recommendation",
        description="Agent with project context should give tailored recommendations",
        suite="memory",
        card=Card.HIGH_PRIESTESS,
        prompt=MEMORY_PROMPTS["context_aware_recommendation"],
        memory_state=fresh_work_context(),
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="relevance",
                    description="Response is tailored to Python/ChromaDB context, not generic",
                    weight=2.0,
                    min_score=0.5,
                ),
                EvalDimension(
                    name="preference_applied",
                    description="Includes code example per stated user preference",
                    weight=1.5,
                ),
            ],
            required_elements=["python", "chroma"],
            pass_threshold=0.65,
        ),
        tags=["memory", "context", "recall"],
    ),

    EvalCase(
        id="memory-rich-context-recall",
        description="Agent with rich project memory should reference it accurately",
        suite="memory",
        card=Card.HIGH_PRIESTESS,
        prompt=MEMORY_PROMPTS["preference_aware_response"],
        memory_state=rich_project_context(),
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="accuracy",
                    description="References Arcana OS, Python stack, or memory architecture accurately",
                    weight=2.0,
                    min_score=0.5,
                ),
                EvalDimension(
                    name="specificity",
                    description="Mentions specific details (tarot, SQLite, local-first), not vague summaries",
                    weight=1.0,
                ),
            ],
            required_elements=["arcana", "memory"],
            pass_threshold=0.6,
        ),
        tags=["memory", "recall", "accuracy"],
    ),

    EvalCase(
        id="memory-confidence-filter-blocks-low",
        description="Low-confidence inferred entries should not surface in context",
        suite="memory",
        card=Card.HERMIT,
        prompt="What are my interests and preferences?",
        memory_state=low_confidence_state(),
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="confidence_filter",
                    description="Does NOT mention distributed systems (low confidence entry). DOES mention bullet points (high confidence).",
                    weight=2.0,
                    min_score=0.5,
                ),
            ],
            required_elements=["bullet"],
            forbidden_elements=["distributed systems"],
            pass_threshold=0.7,
        ),
        tags=["memory", "confidence", "filtering"],
    ),
]
