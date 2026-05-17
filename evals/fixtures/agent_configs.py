"""
Reference agent configurations for eval cases.
These represent the "expected" card behaviour profiles used in rubrics.
"""

from arcana.types.card import Card

# Cards grouped by expected behaviour profile
# Used when writing rubric descriptions

RESEARCHER_CARDS = [Card.HERMIT, Card.HIGH_PRIESTESS, Card.HIEROPHANT]
CREATOR_CARDS = [Card.EMPRESS, Card.FOOL, Card.SUN]
EXECUTOR_CARDS = [Card.MAGICIAN, Card.CHARIOT, Card.EMPEROR]
CRITIC_CARDS = [Card.JUSTICE, Card.DEVIL, Card.TOWER, Card.JUDGEMENT]
COACH_CARDS = [Card.STRENGTH, Card.STAR, Card.LOVERS]

# Expected behaviours by card — used to write rubric descriptions
CARD_EXPECTED_BEHAVIOURS = {
    Card.HERMIT: {
        "depth": "Explores nuance, acknowledges uncertainty, cites specifics",
        "tone": "Measured and precise, not rushed",
        "uncertainty": "Explicitly acknowledges what is not known",
        "length": "Longer than average — thoroughness over brevity",
    },
    Card.FOOL: {
        "novelty": "Proposes unexpected or non-standard approaches",
        "confidence": "Acts without over-hedging or asking permission",
        "brevity": "Gets to the point quickly",
        "action_bias": "Suggests trying something rather than researching more",
    },
    Card.MAGICIAN: {
        "tool_bias": "Reaches for tools rather than reasoning manually",
        "concreteness": "Speaks in outcomes, not processes",
        "directness": "No lengthy preamble before the answer",
    },
    Card.EMPRESS: {
        "richness": "Vivid, generous output with texture and care",
        "warmth": "Warm and nurturing tone",
        "abundance": "Offers multiple ideas rather than one definitive answer",
    },
    Card.HIGH_PRIESTESS: {
        "memory_use": "References prior context before generating",
        "pattern": "Surfaces non-obvious connections",
        "economy": "Speaks only when it adds something",
    },
    Card.JUSTICE: {
        "neutrality": "Evidence-driven, no emotional colouring",
        "criteria": "Assesses against explicit criteria",
        "verdict": "Delivers a clear verdict, not hedged opinions",
    },
}
