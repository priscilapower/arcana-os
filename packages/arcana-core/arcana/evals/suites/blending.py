"""
Card blending eval suite.

Core question: does blending produce a result between the two cards,
or does it break? The blend should be demonstrably different from either
card alone — not identical to the primary, not dominated by the modifier.
"""

from arcana.evals.fixtures.prompts import CREATIVE_PROMPTS, RESEARCH_PROMPTS
from arcana.evals.types import EvalCase, EvalDimension, EvalRubric
from arcana.types.card import Card

BLENDING_CASES: list[EvalCase] = [
    EvalCase(
        id="blend-hermit-empress-balance",
        description="Hermit + Empress blend: deeper than Empress alone, warmer than Hermit alone",
        suite="blending",
        card=Card.HERMIT,
        modifier_cards=[Card.EMPRESS],
        prompt=RESEARCH_PROMPTS["transformer_explained"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="depth",
                    description="Contains specific technical detail — not just a surface overview",
                    weight=1.5,
                ),
                EvalDimension(
                    name="warmth",
                    description="Accessible and warm tone — not cold or purely academic",
                    weight=1.5,
                ),
                EvalDimension(
                    name="balance",
                    description="Neither purely research-oriented nor purely creative — genuinely blended",
                    weight=2.0,
                ),
            ],
            pass_threshold=0.65,
        ),
        tags=["blending", "hermit", "empress"],
    ),
    EvalCase(
        id="blend-fool-justice-creative-rigour",
        description="Fool + Justice: creative ideas with explicit criteria for evaluating them",
        suite="blending",
        card=Card.FOOL,
        modifier_cards=[Card.JUSTICE],
        prompt=CREATIVE_PROMPTS["unconventional_approach"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="novelty",
                    description="Proposes something unexpected — Fool influence present",
                    weight=1.5,
                ),
                EvalDimension(
                    name="criteria",
                    description="Evaluates the idea against some criteria — Justice influence present",
                    weight=1.5,
                ),
                EvalDimension(
                    name="blend",
                    description="Response is both bold AND self-critical — not one or the other",
                    weight=2.0,
                ),
            ],
            pass_threshold=0.6,
        ),
        tags=["blending", "fool", "justice"],
    ),
]
