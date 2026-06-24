"""EmbeddingAdapter ABC — the contract every embedding provider implements.

Embedding generation is deliberately separate from vector storage so the two
concerns swap independently: an embedding provider can change (and the store be
re-indexed) without touching the vector backend, and vice versa.
"""

from abc import ABC, abstractmethod

from arcana.types import AdapterHealth


class EmbeddingError(Exception):
    """Base for embedding-backend failures.

    Concrete adapters raise this (or a subclass) when generation fails, so
    callers can ``except EmbeddingError`` without knowing the provider.
    """


class EmbeddingAdapter(ABC):
    """Converts text into a dense embedding vector. One concrete adapter per provider.

    ``model_name`` and ``dimensions`` are the source of truth for model identity:
    callers read them to decide — before any vector is generated — whether the
    resolved model is the one a store is locked to. They are properties rather
    than fields on the returned vector so that decision needs no embedding call.
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable identifier for the embedding model.

        Must be identical across process restarts: callers use it as a
        persistence key to detect when a store's model has changed.
        """
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Length of the vectors this adapter produces."""
        ...

    @property
    def model_family(self) -> str:
        """Identifier shared by models that embed into the same vector space.

        Two adapters reporting the same ``model_family`` produce interchangeable
        vectors, so one may serve a database the other pinned. Defaults to
        ``model_name`` — each model is its own family unless an adapter
        explicitly declares membership in a shared one.
        """
        return self.model_name

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single string into a dense vector of length ``dimensions``.

        Raises ``EmbeddingError`` (or a subclass) on backend failure — never
        returns an empty or wrong-length vector silently. Callers that want
        graceful degradation gate on ``health_check`` first.
        """
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed many strings, order- and length-preserving (``result[i]`` ↔ ``texts[i]``).

        Default implementation calls ``embed`` serially. Override for providers
        with a native batch endpoint.
        """
        return [await self.embed(text) for text in texts]

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        """Probe whether the provider is reachable and usable.

        Must not raise — return ``AdapterHealth(adapter_id=..., healthy=False,
        message=...)`` on failure, so callers can probe several adapters without
        exception handling.
        """
        ...

    async def ensure_model(self) -> None:  # noqa: B027
        """Download or warm up the model on first use; no-op if already present.

        Default is a no-op for providers that need no local model.
        """
