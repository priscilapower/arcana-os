"""Resilience configuration for memory adapters.

Timeout budgets and circuit-breaker thresholds are per tier, because tiers are
not homogeneous: a keyword read hits local SQLite, while a semantic read calls
an embedding provider that may be remote. Config is optional — an absent file
yields safe defaults, so existing programmatic wiring keeps working untouched.

Loaded from ``~/.arcana/connections/memory-adapters.json``.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path.home() / ".arcana" / "connections" / "memory-adapters.json"


class TierResilienceConfig(BaseModel):
    """Timeout + breaker budget for a single memory tier.

    The keyword/semantic split is per operation: the wrapper applies
    ``semantic_timeout_ms`` when a query would touch the embedding path
    (``retrieval_mode != keyword``) and ``read_timeout_ms`` otherwise, so a slow
    embedder degrades to *no vector tier* rather than a stalled session.
    """

    read_timeout_ms: int = 250
    semantic_timeout_ms: int = 1500
    write_timeout_ms: int = 500
    fail_threshold: int = 3
    reset_after_seconds: float = 30.0


def _default_shared() -> TierResilienceConfig:
    # SHARED/GLOBAL see cross-agent contention — slightly looser than private.
    return TierResilienceConfig(read_timeout_ms=400, write_timeout_ms=600)


class MemoryResilienceConfig(BaseModel):
    """Resilience config for every tier the federation may register.

    ``shared`` is keyed by pool name; a pool with no explicit entry falls back to
    ``default_shared``. ``global_`` uses the ``global`` JSON key (Python reserved
    word) via its field alias.
    """

    private: TierResilienceConfig = Field(default_factory=TierResilienceConfig)
    shared: dict[str, TierResilienceConfig] = Field(default_factory=dict)
    default_shared: TierResilienceConfig = Field(default_factory=_default_shared)
    global_: TierResilienceConfig = Field(default_factory=_default_shared, alias="global")

    model_config = {"populate_by_name": True}

    def for_shared(self, pool_name: str) -> TierResilienceConfig:
        """Config for a named shared pool, falling back to ``default_shared``."""
        return self.shared.get(pool_name, self.default_shared)

    @classmethod
    def load(cls, path: Path | None = None) -> "MemoryResilienceConfig":
        """Load config from disk. A missing file yields all defaults (never raises)."""
        target = path or DEFAULT_CONFIG_PATH
        if not target.exists():
            return cls()
        data = json.loads(target.read_text(encoding="utf-8"))
        return cls.model_validate(data)
