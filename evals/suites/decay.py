"""
Memory decay eval suite.

Core question: do stale entries rank below fresh ones at retrieval?
Does the agent prefer recent information over old information on the same topic?
"""

from arcana.evals.fixtures.memory_states import stale_vs_fresh_work
from arcana.evals.fixtures.prompts import MEMORY_PROMPTS
from arcana.evals.types import EvalCase, EvalDimension, EvalRubric
from arcana.types.card import Card

DECAY_CASES: list[EvalCase] = [

    EvalCase(
        id="decay-fresh-beats-stale",
        description="Fresh memory (startup CTO) should surface over stale memory (Google)",
        suite="decay",
        card=Card.HERMIT,
        prompt=MEMORY_PROMPTS["stale_vs_fresh"],
        memory_state=stale_vs_fresh_work(),
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="recency",
                    description="References startup/CTO context, not Google. Decay should rank fresh entry higher.",
                    weight=2.0,
                    min_score=0.6,
                ),
            ],
            required_elements=["startup"],
            forbidden_elements=["google"],
            pass_threshold=0.7,
        ),
        tags=["decay", "recency", "staleness"],
    ),

    EvalCase(
        id="decay-no-memory-no-hallucination",
        description="With no memory seeded, agent should not hallucinate work context",
        suite="decay",
        card=Card.HERMIT,
        prompt=MEMORY_PROMPTS["work_context"],
        memory_state=[],  # no memory seeded
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="no_hallucination",
                    description="Acknowledges it doesn't have context about the user's work. Does not invent details.",
                    weight=2.0,
                    min_score=0.6,
                ),
            ],
            required_elements=["don't have", "not sure", "no information", "haven't"],
            pass_threshold=0.7,
        ),
        tags=["decay", "hallucination", "empty-memory"],
        # At least one of the required_elements should match — rubric is OR not AND
        # TODO: EvalRubric should support required_any vs required_all
    ),
]
