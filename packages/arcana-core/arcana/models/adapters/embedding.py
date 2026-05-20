"""EmbeddingAdapter ABC — separated from VectorAdapter so the two concerns
can be swapped independently.

Phase 1:  OllamaEmbeddingAdapter (nomic-embed-text) + SqliteVecAdapter
Phase 2:  OllamaEmbeddingAdapter OR TransformersEmbeddingAdapter + SqliteVecAdapter
Phase 3:  OpenAIEmbeddingAdapter + PgVectorAdapter (or QdrantAdapter)

Keeping embedding generation separate from vector storage means you can switch
embedding providers (and re-index) without touching the vector store, and vice versa.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str
    dimensions: int = field(init=False)

    def __post_init__(self) -> None:
        self.dimensions = len(self.vector)


class EmbeddingAdapter(ABC):
    """
    Converts text into a dense embedding vector.

    Each concrete adapter targets one provider:
      - OllamaEmbeddingAdapter   → nomic-embed-text via Ollama (default Phase 1)
      - TransformersEmbeddingAdapter → in-process transformers.js fallback
      - OpenAIEmbeddingAdapter   → text-embedding-3-small via OpenAI API (Phase 3)
    """

    model_id: str = "unknown"
    dimensions: int = 768           # override per adapter

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """Embed a single string. Returns a dense vector."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """
        Embed multiple strings. Default implementation calls embed() serially.
        Override for providers that support native batch endpoints (OpenAI, Cohere).
        """
        results = []
        for text in texts:
            results.append(await self.embed(text))
        return results

    async def health_check(self) -> bool:
        """Return True if the embedding provider is reachable."""
        try:
            result = await self.embed("health check")
            return len(result.vector) == self.dimensions
        except Exception:
            return False
