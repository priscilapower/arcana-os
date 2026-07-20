"""Tunables for tool execution.

The per-tool timeout and the tool-loop iteration cap ship with sensible defaults
but are **overridable via environment variables**, so code built on top of Arcana
can tune tool execution without waiting for a release. :class:`ToolTunables` is a
``pydantic-settings`` model: it reads ``ARCANA_TOOLS_*`` env vars once at import
(typed, coerced, and validated — a malformed or out-of-bounds value fails fast)
and its values seed the module-level constants the rest of the package imports.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolTunables(BaseSettings):
    """Tool-execution knobs, overridable via ``ARCANA_TOOLS_*`` env vars.

    Instantiated once into the module constants below. Unrelated variables that
    happen to share the prefix are ignored rather than rejected.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_TOOLS_", extra="ignore")

    # Per-tool execution timeout (seconds). A slow or hanging tool fails as a
    # ToolResult rather than wedging the run; must be positive.
    timeout_s: float = Field(default=30.0, gt=0.0)

    # Hard ceiling on model→tool→model round-trips in a single run. At least one
    # pass always runs, so the floor is 1.
    max_iterations: int = Field(default=5, ge=1)


TUNABLES = ToolTunables()

DEFAULT_TOOL_TIMEOUT_S = TUNABLES.timeout_s
DEFAULT_MAX_TOOL_ITERATIONS = TUNABLES.max_iterations
