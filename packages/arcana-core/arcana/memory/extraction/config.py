"""Tunables and config for memory extraction.

The scoring knobs — confidence caps, importance baselines, the content-length
cap — ship with sensible defaults but are **overridable via environment
variables**, so code built on top of Arcana can tune extraction without waiting
for a release. :class:`ExtractionTunables` is a ``pydantic-settings`` model: it
reads ``ARCANA_EXTRACTION_*`` env vars once at import (typed, coerced, and
validated — a malformed value fails fast) and its values seed the module-level
constants the rest of the package imports.

``ExtractionConfig`` (the ``extraction`` sub-block of the ``memory`` config) sits
one level above these: an explicit value in ``config.json`` wins, otherwise the
field defaults to the env-or-built-in constant. Precedence, high to low:
``config.json`` → ``ARCANA_EXTRACTION_*`` env → built-in default.
"""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from arcana.types import ExtractionStrategy


class ExtractionTunables(BaseSettings):
    """Extraction scoring knobs, overridable via ``ARCANA_EXTRACTION_*`` env vars.

    Instantiated once into the module constants below. Unrelated variables that
    happen to share the prefix (e.g. ``ARCANA_EXTRACTION_TEST_MODEL`` used by the
    LLM tests) are ignored rather than rejected.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_EXTRACTION_", extra="ignore")

    # Confidence. Agent-asserted text is capped *strictly below* 1.0 so a
    # hallucinated fact can always be overridden by better evidence later — the
    # anti-poisoning invariant. The bound is enforced, not merely defaulted: a cap
    # of 1.0 would defeat the invariant, so it fails fast at load. A user statement
    # is trusted higher (up to 1.0); sub-threshold entries are dropped before write.
    agent_confidence_cap: float = Field(default=0.7, ge=0.0, lt=1.0)
    user_confirmed_confidence: float = Field(default=0.9, gt=0.0, le=1.0)
    min_confidence_to_store: float = Field(default=0.3, ge=0.0, le=1.0)

    # Importance baselines (0.0–1.0). A session summary outranks a raw turn; the
    # signal bonus applies to imperative/"remember" language or a pin.
    base_importance_episodic: float = Field(default=0.4, ge=0.0, le=1.0)
    base_importance_semantic: float = Field(default=0.5, ge=0.0, le=1.0)
    base_importance_procedural: float = Field(default=0.4, ge=0.0, le=1.0)
    summary_importance: float = Field(default=0.7, ge=0.0, le=1.0)
    signal_bonus: float = Field(default=0.2, ge=0.0, le=1.0)

    # Per-entry content cap — keeps one memory from swallowing a whole transcript.
    # The right value is empirical; validate changes with the LLM-backed tests.
    max_content: int = Field(default=600, gt=0)


TUNABLES = ExtractionTunables()

DEFAULT_AGENT_CONFIDENCE_CAP = TUNABLES.agent_confidence_cap
USER_CONFIRMED_CONFIDENCE = TUNABLES.user_confirmed_confidence
DEFAULT_MIN_CONFIDENCE_TO_STORE = TUNABLES.min_confidence_to_store

BASE_IMPORTANCE_EPISODIC = TUNABLES.base_importance_episodic
BASE_IMPORTANCE_SEMANTIC = TUNABLES.base_importance_semantic
BASE_IMPORTANCE_PROCEDURAL = TUNABLES.base_importance_procedural
SUMMARY_IMPORTANCE = TUNABLES.summary_importance
SIGNAL_BONUS = TUNABLES.signal_bonus

MAX_ENTRY_CONTENT = TUNABLES.max_content


class ExtractionConfig(BaseModel):
    """The ``extraction`` sub-block of the ``memory`` config.

    ``strategy`` selects the extractor; ``llm`` needs a model/gateway or it is
    forced back to ``heuristic`` by ``build_extractor``. Numeric fields default
    to the env-overridable tunables above and carry the same bounds — notably
    ``agent_confidence_cap`` must stay strictly below ``1.0`` (the anti-poisoning
    invariant), so a config setting it to ``1.0`` is rejected at load.
    """

    strategy: ExtractionStrategy = ExtractionStrategy.HEURISTIC
    agent_confidence_cap: float = Field(default=DEFAULT_AGENT_CONFIDENCE_CAP, ge=0.0, lt=1.0)
    summarise_on_close: bool = True
    min_confidence_to_store: float = Field(default=DEFAULT_MIN_CONFIDENCE_TO_STORE, ge=0.0, le=1.0)
