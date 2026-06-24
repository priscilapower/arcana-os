"""EmbeddingGateway — resolves which embedding adapter serves a database.

A database is *pinned* to the model that wrote its first embedding (recorded in
its ``EmbeddingMeta``). The gateway returns the adapter for that model when it is
healthy. If the exact model is unavailable, it falls back to another healthy
adapter in the **same model family** — one whose vectors are interchangeable —
before giving up. Returning ``None`` signals the caller to use keyword (FTS5)
search instead. It never silently substitutes a model from a different family,
which would corrupt similarity scores against existing vectors.
"""

from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.types import EmbeddingMeta


class EmbeddingGateway:
    """Picks the embedding adapter for a database, honouring its model pin."""

    def __init__(self, adapters: list[EmbeddingAdapter]) -> None:
        # Priority order matters for unpinned databases: the first healthy
        # adapter wins and the database is pinned to it.
        self._adapters = adapters

    async def resolve(self, db_meta: EmbeddingMeta | None) -> EmbeddingAdapter | None:
        """Return the adapter to embed with, or ``None`` to fall back to FTS5."""
        health: dict[int, bool] = {}

        async def healthy(adapter: EmbeddingAdapter) -> bool:
            # Memoise per call: health_check may do real I/O, and the family
            # pass would otherwise re-probe adapters already checked.
            key = id(adapter)
            if key not in health:
                health[key] = (await adapter.health_check()).healthy
            return health[key]

        if db_meta is None:
            # New database: pin to the first healthy adapter, in priority order.
            for adapter in self._adapters:
                if await healthy(adapter):
                    await adapter.ensure_model()
                    return adapter
            return None

        # Pinned database — prefer the exact model that wrote it.
        for adapter in self._adapters:
            if adapter.model_name == db_meta.model_name and await healthy(adapter):
                return adapter

        # Exact model unavailable: fall back within the same family, whose
        # vectors are interchangeable, so a pinned database keeps doing semantic
        # search (e.g. across tiers) instead of dropping to keyword-only.
        family = self._family_of(db_meta.model_name)
        if family is not None:
            for adapter in self._adapters:
                if adapter.model_family == family and await healthy(adapter):
                    await adapter.ensure_model()
                    return adapter

        return None  # no compatible adapter healthy → caller uses FTS5

    def _family_of(self, model_name: str) -> str | None:
        """The family of a pinned model, looked up via the adapter that owns it."""
        for adapter in self._adapters:
            if adapter.model_name == model_name:
                return adapter.model_family
        return None
