"""Tunables for task routing.

The router records only the first slice of a task on each :class:`RoutingDecision`
(the rest of the task is not useful in an audit and could leak content). That
slice length ships with a sensible default but is **overridable via environment
variable**, so an operator can widen or tighten it without a code change.
:class:`RoutingTunables` is a ``pydantic-settings`` model: it reads
``ARCANA_ROUTING_*`` env vars once at import (typed, coerced, and bounds-checked
so a bad value fails fast) and seeds the module constant the package exposes.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RoutingTunables(BaseSettings):
    """Routing knobs, overridable via ``ARCANA_ROUTING_*`` env vars.

    Instantiated once into the module constant below. Unrelated variables that
    happen to share the prefix are ignored rather than rejected.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_ROUTING_", extra="ignore")

    # How many leading characters of the task to keep on a RoutingDecision. The
    # preview is diagnostic only; must be positive.
    task_preview_chars: int = Field(default=200, ge=1)

    # Reflex classifier knobs.
    # A pick below this confidence still routes, but is flagged low-confidence
    # and surfaced to the user (0.0–1.0).
    reflex_confidence_min: float = Field(default=0.5, ge=0.0, le=1.0)
    # Hard ceiling on the reflex model call; on breach the route degrades to the
    # deterministic default rather than hanging. Seconds; must be positive.
    reflex_timeout_s: float = Field(default=10.0, gt=0.0)
    # Per-candidate cap on the card-ingredient text folded into the classifier
    # prompt, so full ingredients stay within a sane token budget. Must be positive.
    reflex_max_ingredient_chars: int = Field(default=600, ge=1)
    # Cap on the pick's reasoning stored on the RoutingDecision, so a chatty model
    # can't bloat the audit with an essay. Must be positive.
    reflex_reasoning_max_chars: int = Field(default=280, ge=1)


TUNABLES = RoutingTunables()

DEFAULT_TASK_PREVIEW_CHARS = TUNABLES.task_preview_chars
DEFAULT_REFLEX_CONFIDENCE_MIN = TUNABLES.reflex_confidence_min
DEFAULT_REFLEX_TIMEOUT_S = TUNABLES.reflex_timeout_s
DEFAULT_REFLEX_MAX_INGREDIENT_CHARS = TUNABLES.reflex_max_ingredient_chars
DEFAULT_REFLEX_REASONING_MAX_CHARS = TUNABLES.reflex_reasoning_max_chars
