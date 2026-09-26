"""The reflex classifier — a single local model call that ranks the pool.

When no rule matched and a reflex model is present, :class:`ReflexClassifier`
asks that model to pick the best agent for a task. The prompt carries each
candidate's role and full card ``prompt_ingredients`` (bounded to a token
budget); the model returns a structured :class:`ReflexPick` (agent, confidence,
one-line reasoning).

Everything here is fail-safe: an empty pool, a timeout, a malformed reply, or a
pick that names an agent outside the pool all resolve to ``None`` so the engine
falls through to its deterministic default. The classifier never raises, and it
always spends the *reflex* model handle it was given — never a reasoning model.
"""

import asyncio
import json
import logging
from collections.abc import Iterator
from functools import cached_property
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from arcana.cards.registry import get_registry
from arcana.models.adapters.base import CompletionRequest
from arcana.models.errors import ModelError
from arcana.types import Agent, CapabilityTier, PromptIngredients, ReflexPick
from arcana.world.config import (
    DEFAULT_REFLEX_CONFIDENCE_MIN,
    DEFAULT_REFLEX_MAX_INGREDIENT_CHARS,
    DEFAULT_REFLEX_REASONING_MAX_CHARS,
    DEFAULT_REFLEX_TIMEOUT_S,
)

if TYPE_CHECKING:
    from arcana.models.gateway import ModelGateway

logger = logging.getLogger("arcana.world.reflex")

_DECODER = json.JSONDecoder()

_SYSTEM_PROMPT = (
    "You are the router for a team of AI agents. Given a task and a numbered list "
    "of candidate agents (each with a role and how it works), pick the single best "
    "agent for the task. Respond with ONLY a JSON object and nothing else, of the "
    'form {"agent_id": "<the id string of your pick>", "confidence": <0.0-1.0>, '
    '"reasoning": "<one short sentence>"}. Choose an agent_id exactly as given.'
)


class ReflexClassifier:
    """Ranks the candidate pool with one reflex-model call.

    Holds the model gateway and the *reflex* model reference to spend; its
    :attr:`tier` records the capability tier it was built for (a ``NO_MODEL``
    classifier never calls the model). Thresholds and bounds default to the
    env-overridable routing tunables.
    """

    def __init__(
        self,
        gateway: "ModelGateway",
        reflex_model: str,
        *,
        tier: CapabilityTier = CapabilityTier.REFLEX,
        confidence_min: float = DEFAULT_REFLEX_CONFIDENCE_MIN,
        timeout_s: float = DEFAULT_REFLEX_TIMEOUT_S,
        max_ingredient_chars: int = DEFAULT_REFLEX_MAX_INGREDIENT_CHARS,
        reasoning_max_chars: int = DEFAULT_REFLEX_REASONING_MAX_CHARS,
    ) -> None:
        self._gateway = gateway
        self._reflex_model = reflex_model
        self.tier = tier
        self.confidence_min = confidence_min
        self._timeout_s = timeout_s
        self._max_ingredient_chars = max_ingredient_chars
        self._reasoning_max_chars = reasoning_max_chars

    @cached_property
    def model_connection_id(self) -> UUID | None:
        """The reflex model's connection id, or ``None`` if it can't be resolved.

        Stamped onto a ``REFLEX`` decision as ``reflex_model_id``. Resolution is
        offline (parses the reference, looks up the connection store) — no model
        call — so a misconfigured reference degrades to ``None`` rather than
        raising. Cached because the reference is fixed for the classifier's
        lifetime, so the id is resolved at most once however many routes reuse it.
        """
        try:
            return self._gateway.resolve(self._reflex_model).id
        except (ValueError, ModelError):
            return None

    async def classify(self, task: str, candidates: list[Agent]) -> ReflexPick | None:
        """Pick the best candidate for ``task``, or ``None`` to fall through.

        Returns ``None`` on an empty pool, a timeout, a malformed reply, or a
        pick outside the pool — every failure mode degrades to the deterministic
        default. Never raises.
        """
        if not candidates:
            return None

        pool_ids = {agent.id for agent in candidates}
        request = CompletionRequest(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._build_prompt(task, candidates)}],
            temperature=0.0,
            max_tokens=256,
        )
        try:
            response = await asyncio.wait_for(
                self._gateway.complete(self._reflex_model, request),
                timeout=self._timeout_s,
            )
        except (TimeoutError, ModelError):
            logger.warning("reflex classifier call failed or timed out; falling through to default")
            return None
        except Exception:  # noqa: BLE001 — a classifier fault must never strand a route
            logger.warning("reflex classifier raised unexpectedly; falling through to default", exc_info=True)
            return None

        pick = self._parse(response.content, pool_ids)
        if pick is None:
            logger.warning("reflex classifier returned an unusable reply; falling through to default")
        return pick

    def _parse(self, content: str, pool_ids: set[UUID]) -> ReflexPick | None:
        """Coerce a model reply into a :class:`ReflexPick`, or ``None``.

        Scans ``content`` for JSON objects (tolerating prose, code fences, and a
        model's ``<think>`` preamble around the payload) and returns the first one
        that validates as a pick naming an in-pool agent. A confidence outside
        0–1 is clamped rather than rejected; a hallucinated (out-of-pool) id can
        never route.
        """
        for obj in _json_objects(content):
            try:
                raw = _RawPick.model_validate(obj)
            except ValidationError:
                continue
            if raw.agent_id not in pool_ids:
                continue
            confidence = min(1.0, max(0.0, raw.confidence))
            return ReflexPick(
                agent_id=raw.agent_id,
                confidence=confidence,
                reasoning=raw.reasoning[: self._reasoning_max_chars],
            )
        return None

    # ------------------------------------------------------------------
    # Prompt building & parsing
    # ------------------------------------------------------------------

    def _build_prompt(self, task: str, candidates: list[Agent]) -> str:
        blocks = [self._candidate_block(agent) for agent in candidates]
        roster = "\n\n".join(blocks)
        return f"Task:\n{task}\n\nCandidate agents:\n{roster}\n\nReturn your pick as JSON."

    def _candidate_block(self, agent: Agent) -> str:
        """One candidate's block: id, name, role, and full card ingredients.

        The ingredient text is bounded per candidate so a large pool with full
        ingredients still fits a sane token budget.
        """
        role = ""
        ingredients: PromptIngredients | None = None
        try:
            archetype = get_registry().get(agent.card).archetype
            role = archetype.role
            ingredients = archetype.prompt_ingredients
        except (ValueError, KeyError):
            # An unknown card is unexpected, but the pick must not depend on it;
            # fall back to name-only so this candidate is still selectable.
            logger.warning("card %s for agent %s has no definition; using name only", agent.card, agent.id)

        lines = [f"- id: {agent.id}", f"  name: {agent.name}"]
        if role:
            lines.append(f"  role: {role}")
        if ingredients is not None:
            lines.append(f"  tone: {ingredients.tone}")
            lines.append(f"  approach: {ingredients.approach}")
            if ingredients.priorities:
                lines.append(f"  priorities: {', '.join(ingredients.priorities)}")
            if ingredients.avoid:
                lines.append(f"  avoid: {', '.join(ingredients.avoid)}")
        block = "\n".join(lines)
        return block[: self._max_ingredient_chars]


class _RawPick(BaseModel):
    """The classifier's on-the-wire reply, parsed leniently.

    ``agent_id`` must be present and UUID-shaped (pydantic coerces the string);
    ``confidence`` is coerced to float and clamped by the caller; extra keys are
    ignored so a chatty model can't break parsing.
    """

    model_config = ConfigDict(extra="ignore")

    agent_id: UUID
    confidence: float = 0.0
    reasoning: str = ""


def _json_objects(content: str) -> Iterator[Any]:
    """Yield each parseable JSON value embedded in ``content``, left to right.

    Scans every ``{`` and tries to decode a JSON value starting there, skipping
    the consumed span on success and advancing to the next ``{`` on failure. This
    finds the pick even when the model wraps it in prose, a code fence, or a
    ``<think>`` block that itself contains braces — where a naive first-``{`` /
    last-``}`` slice would splice unrelated text into the payload.
    """
    idx = content.find("{")
    while idx != -1:
        try:
            obj, end = _DECODER.raw_decode(content, idx)
        except json.JSONDecodeError:
            idx = content.find("{", idx + 1)
            continue
        yield obj
        idx = content.find("{", end)
