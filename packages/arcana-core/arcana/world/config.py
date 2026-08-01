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


TUNABLES = RoutingTunables()

DEFAULT_TASK_PREVIEW_CHARS = TUNABLES.task_preview_chars
