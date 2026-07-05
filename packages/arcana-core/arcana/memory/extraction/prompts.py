"""System prompts for the LLM extraction strategy.

Kept separate from the extractor logic so the wording can be iterated (and, in
future, localised or A/B-tested) without touching control flow.
"""

EXTRACT_SYSTEM = (
    "You extract durable memories from a single assistant turn. "
    "Return ONLY a JSON array (no prose, no code fence). Each element is an object "
    '{"type": "episodic|semantic|procedural", "content": "<one concise sentence>", '
    '"importance": <0.0-1.0>}. '
    "Use 'semantic' for durable facts or user preferences, 'procedural' for how-to "
    "steps, and 'episodic' for what happened this turn. Emit at most 4 elements; "
    "return [] when nothing is worth remembering."
)

SUMMARISE_SYSTEM = (
    "Summarise this conversation in one or two sentences, capturing durable facts, "
    "decisions, and preferences. Return only the summary text."
)
