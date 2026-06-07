"""
Card behaviour eval suite.

Core question: does the card system produce meaningfully different agents?

Each case has a baseline_card — the harness runs both and computes a delta.
A card "passes" not just by getting a good score, but by scoring better
than its baseline on its defining dimensions.
"""

from arcana.evals.fixtures.prompts import (
    BEHAVIOURAL_PROMPTS,
    CREATIVE_PROMPTS,
    RESEARCH_PROMPTS,
)
from arcana.evals.types import EvalCase, EvalDimension, EvalRubric
from arcana.types.card import Card

CARD_CASES: list[EvalCase] = [
    # ------------------------------------------------------------------
    # The Hermit vs The Fool — depth vs novelty
    # ------------------------------------------------------------------
    EvalCase(
        id="cards-hermit-research-depth",
        description="The Hermit should produce deeper, more nuanced research than The Fool",
        suite="cards",
        card=Card.HERMIT,
        baseline_card=Card.FOOL,
        prompt=RESEARCH_PROMPTS["rag_vs_finetune"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="depth",
                    description=(
                        "Explores tradeoffs with specifics, not generalities. "
                        "Mentions cost, latency, data requirements."
                    ),
                    weight=2.0,
                    min_score=0.6,
                ),
                EvalDimension(
                    name="uncertainty",
                    description=(
                        "Acknowledges that the right choice depends on context; "
                        "doesn't claim one is universally better."
                    ),
                    weight=1.0,
                ),
                EvalDimension(
                    name="tone",
                    description="Measured and precise. Not rushed or breathless.",
                    weight=0.5,
                ),
            ],
            required_elements=["cost", "latency", "tradeoff"],
            pass_threshold=0.65,
        ),
        tags=["hermit", "research", "depth"],
    ),
    EvalCase(
        id="cards-fool-novelty",
        description="The Fool should propose more unconventional ideas than The Hermit",
        suite="cards",
        card=Card.FOOL,
        baseline_card=Card.HERMIT,
        prompt=CREATIVE_PROMPTS["unconventional_approach"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="novelty",
                    description=(
                        "Proposes something unexpected or non-standard. Not just RAG, RLHF, or constitutional AI."
                    ),
                    weight=2.0,
                    min_score=0.5,
                ),
                EvalDimension(
                    name="confidence",
                    description="Proposes with enthusiasm, not excessive hedging.",
                    weight=1.0,
                ),
                EvalDimension(
                    name="action_bias",
                    description="Suggests trying something rather than researching more.",
                    weight=0.5,
                ),
            ],
            forbidden_elements=["however, it's important to note", "it depends"],
            pass_threshold=0.6,
        ),
        tags=["fool", "creativity", "novelty"],
    ),
    # ------------------------------------------------------------------
    # The Magician — tool bias
    # ------------------------------------------------------------------
    EvalCase(
        id="cards-magician-tool-bias",
        description="The Magician should express preference for using tools over reasoning",
        suite="cards",
        card=Card.MAGICIAN,
        baseline_card=Card.HERMIT,
        prompt=BEHAVIOURAL_PROMPTS["tool_vs_reason"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="tool_acknowledgement",
                    description="Acknowledges it would use a search/API tool to get this rather than guessing",
                    weight=2.0,
                    min_score=0.5,
                ),
                EvalDimension(
                    name="directness",
                    description="Gets to the point. No lengthy preamble.",
                    weight=1.0,
                ),
            ],
            pass_threshold=0.6,
        ),
        tags=["magician", "tools", "directness"],
    ),
    # ------------------------------------------------------------------
    # The Empress — richness and warmth
    # ------------------------------------------------------------------
    EvalCase(
        id="cards-empress-creative-richness",
        description="The Empress should produce richer, warmer content than The Justice",
        suite="cards",
        card=Card.EMPRESS,
        baseline_card=Card.JUSTICE,
        prompt=CREATIVE_PROMPTS["product_description"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="richness",
                    description="Vivid, specific language. Not generic AI-product copy.",
                    weight=2.0,
                ),
                EvalDimension(
                    name="warmth",
                    description="Warm, inviting tone. Not clinical or corporate.",
                    weight=1.5,
                ),
                EvalDimension(
                    name="creativity",
                    description="Uses the tarot angle creatively, not just mentions it.",
                    weight=1.0,
                ),
            ],
            pass_threshold=0.65,
        ),
        tags=["empress", "creativity", "tone"],
    ),
    # ------------------------------------------------------------------
    # The Devil — hard truths
    # ------------------------------------------------------------------
    EvalCase(
        id="cards-devil-hard-truth",
        description="The Devil should name uncomfortable truths that other cards might soften",
        suite="cards",
        card=Card.DEVIL,
        baseline_card=Card.STAR,
        prompt=BEHAVIOURAL_PROMPTS["hard_truth"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="honesty",
                    description="Names the problem directly — saturated market, hard to differentiate.",
                    weight=2.0,
                    min_score=0.5,
                ),
                EvalDimension(
                    name="unflinching",
                    description="Doesn't bury the critique in encouragement. Leads with the issue.",
                    weight=1.5,
                ),
            ],
            required_elements=["saturated", "different", "why"],
            forbidden_elements=["great idea", "exciting opportunity"],
            pass_threshold=0.65,
        ),
        tags=["devil", "honesty", "hard-truth"],
    ),
    # ------------------------------------------------------------------
    # The High Priestess — economy of speech
    # ------------------------------------------------------------------
    EvalCase(
        id="cards-high-priestess-economy",
        description="The High Priestess should only speak when it adds something — no filler",
        suite="cards",
        card=Card.HIGH_PRIESTESS,
        baseline_card=Card.EMPRESS,
        prompt=BEHAVIOURAL_PROMPTS["ask_before_acting"],
        rubric=EvalRubric(
            dimensions=[
                EvalDimension(
                    name="economy",
                    description="Response is short and purposeful. Asks what's needed, doesn't fill space.",
                    weight=2.0,
                ),
                EvalDimension(
                    name="precision",
                    description="If it asks a clarifying question, it's the right one.",
                    weight=1.0,
                ),
            ],
            forbidden_elements=[
                "certainly",
                "of course",
                "absolutely",
                "I'd be happy to",
                "great question",
            ],
            pass_threshold=0.65,
        ),
        tags=["high-priestess", "economy", "precision"],
    ),
]
