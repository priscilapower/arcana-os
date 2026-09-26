"""Tests for the reflex classifier — picking, parsing, and fail-safe fall-through."""

import json
from uuid import uuid4

from arcana.models.errors import ModelUnavailableError
from arcana.types import Agent, Card
from arcana.world.reflex import ReflexClassifier
from tests.support.model import RoutingModel, reflex_reply

REFLEX_MODEL = "ollama/reflex"


def _agent(name: str, *, card: Card = Card.FOOL) -> Agent:
    return Agent(name=name, card=card, system_prompt="p", temperature=0.5)


def _reply(agent: Agent, *, confidence: float = 0.8, reasoning: str = "best fit") -> str:
    return reflex_reply(agent.id, confidence=confidence, reasoning=reasoning)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_picks_in_pool_agent_from_valid_json():
    a = _agent("alpha")
    b = _agent("bravo")
    model = RoutingModel(content=_reply(b, confidence=0.9, reasoning="bravo handles it"))
    clf = ReflexClassifier(model, REFLEX_MODEL)
    pick = await clf.classify("route me", [a, b])
    assert pick is not None
    assert pick.agent_id == b.id
    assert pick.confidence == 0.9
    assert pick.reasoning == "bravo handles it"
    assert model.completions == 1


async def test_prompt_carries_role_and_card_ingredients():
    a = _agent("scout", card=Card.FOOL)
    model = RoutingModel(content=_reply(a))
    clf = ReflexClassifier(model, REFLEX_MODEL)
    await clf.classify("explore the repo", [a])
    prompt = model.seen[0].messages[0]["content"]
    # The candidate block folds in the card's role and full prompt ingredients.
    assert str(a.id) in prompt
    assert "Explorer / Autonomous Agent" in prompt  # Fool's archetype role
    assert "tone:" in prompt and "approach:" in prompt


# ---------------------------------------------------------------------------
# Confidence handling
# ---------------------------------------------------------------------------


async def test_confidence_above_one_is_clamped():
    a = _agent("alpha")
    model = RoutingModel(content=_reply(a, confidence=1.7))
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a])
    assert pick is not None
    assert pick.confidence == 1.0


async def test_confidence_below_zero_is_clamped():
    a = _agent("alpha")
    model = RoutingModel(content=_reply(a, confidence=-0.4))
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a])
    assert pick is not None
    assert pick.confidence == 0.0


async def test_low_confidence_pick_is_still_returned():
    a = _agent("alpha")
    model = RoutingModel(content=_reply(a, confidence=0.1))
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a])
    assert pick is not None
    assert pick.agent_id == a.id  # the flag lives on the decision; the classifier still picks


async def test_reasoning_is_truncated():
    a = _agent("alpha")
    model = RoutingModel(content=_reply(a, reasoning="x" * 5000))
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a])
    assert pick is not None
    assert len(pick.reasoning) <= 280


# ---------------------------------------------------------------------------
# Malformed / unusable output → fall through (None)
# ---------------------------------------------------------------------------


async def test_non_json_reply_falls_through():
    a = _agent("alpha")
    model = RoutingModel(content="I think alpha is best, honestly.")
    assert await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a]) is None


async def test_pick_outside_pool_falls_through():
    a = _agent("alpha")
    stranger = json.dumps({"agent_id": str(uuid4()), "confidence": 0.9, "reasoning": "ghost"})
    model = RoutingModel(content=stranger)
    assert await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a]) is None


async def test_missing_agent_id_falls_through():
    a = _agent("alpha")
    model = RoutingModel(content=json.dumps({"confidence": 0.9, "reasoning": "no id"}))
    assert await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a]) is None


async def test_json_embedded_in_prose_is_extracted():
    a = _agent("alpha")
    wrapped = f"Sure! Here is my pick:\n```json\n{_reply(a)}\n```\nHope that helps."
    model = RoutingModel(content=wrapped)
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a])
    assert pick is not None
    assert pick.agent_id == a.id


async def test_pick_survives_think_block_with_braces():
    a = _agent("alpha")
    b = _agent("bravo")
    # A reasoning model reasons in a <think> block that itself contains braces,
    # then emits the real pick. First-{/last-} slicing would splice them together.
    content = (
        "<think> comparing {alpha} vs {bravo}; the set {x, y} suggests bravo </think>\n"
        f"{_reply(b, reasoning='bravo it is')}"
    )
    model = RoutingModel(content=content)
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a, b])
    assert pick is not None
    assert pick.agent_id == b.id


async def test_first_valid_but_non_pick_object_is_skipped():
    a = _agent("alpha")
    # A stray JSON object with no agent_id precedes the real pick; the scanner
    # must skip it rather than give up.
    content = f'{{"note": "thinking"}} then: {_reply(a)}'
    model = RoutingModel(content=content)
    pick = await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a])
    assert pick is not None
    assert pick.agent_id == a.id


# ---------------------------------------------------------------------------
# Fail-safe: empty pool, errors, timeout
# ---------------------------------------------------------------------------


async def test_empty_pool_makes_no_call():
    model = RoutingModel(content="{}")
    assert await ReflexClassifier(model, REFLEX_MODEL).classify("t", []) is None
    assert model.completions == 0


async def test_model_error_falls_through():
    a = _agent("alpha")
    model = RoutingModel(error=ModelUnavailableError("down"))
    assert await ReflexClassifier(model, REFLEX_MODEL).classify("t", [a]) is None


async def test_timeout_falls_through():
    a = _agent("alpha")
    # The call outlasts the classifier's tiny timeout → degrade to None, not hang.
    model = RoutingModel(content=_reply(a), delay=5.0)
    clf = ReflexClassifier(model, REFLEX_MODEL, timeout_s=0.01)
    assert await clf.classify("t", [a]) is None


# ---------------------------------------------------------------------------
# Model connection id & ingredient budget
# ---------------------------------------------------------------------------


async def test_model_connection_id_resolves():
    fixed = uuid4()
    model = RoutingModel(connection_id=fixed)
    assert ReflexClassifier(model, REFLEX_MODEL).model_connection_id == fixed


async def test_model_connection_id_none_when_unresolvable():
    model = RoutingModel(resolve_error=ValueError("no such connection"))
    assert ReflexClassifier(model, REFLEX_MODEL).model_connection_id is None


async def test_ingredient_budget_caps_candidate_block():
    a = _agent("alpha")
    model = RoutingModel(content=_reply(a))
    # A tiny per-candidate cap truncates the block before the role/ingredients.
    clf = ReflexClassifier(model, REFLEX_MODEL, max_ingredient_chars=12)
    await clf.classify("t", [a])
    prompt = model.seen[0].messages[0]["content"]
    assert "Explorer / Autonomous Agent" not in prompt
