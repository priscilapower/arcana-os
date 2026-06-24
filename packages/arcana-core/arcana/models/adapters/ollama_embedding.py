"""OllamaEmbeddingAdapter — local embeddings via Ollama."""

from typing import Required, TypedDict

import httpx

from arcana.models.adapters.embedding import EmbeddingAdapter, EmbeddingError
from arcana.types import AdapterHealth

_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_DIMENSIONS = 768
_DEFAULT_ENDPOINT = "http://localhost:11434"

# Ollama's nomic-embed-text and fastembed's nomic-embed-text-v1.5 embed into the
# same vector space; they report this shared family so a database pinned to one
# can fall back to the other. Must stay identical to the fastembed adapter's value.
_NOMIC_FAMILY = "nomic-text-v1.5"


class _EmbedPayload(TypedDict, total=False):
    model: Required[str]
    input: Required[str | list[str]]


class OllamaEmbeddingAdapter(EmbeddingAdapter):
    """Generates embeddings from a local Ollama instance.

    Defaults to ``nomic-embed-text`` (768-dimensional). Talks to Ollama's
    ``/api/embed`` endpoint, which embeds a single string or a batch in one
    request; ``embed_batch`` uses that batch form directly.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        dimensions: int = _DEFAULT_DIMENSIONS,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self.endpoint = endpoint.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_family(self) -> str:
        return _NOMIC_FAMILY if self._model == _DEFAULT_MODEL else self._model

    async def aclose(self) -> None:
        await self._client.aclose()

    def _translate(self, exc: Exception) -> EmbeddingError:
        if isinstance(exc, httpx.ConnectError):
            return EmbeddingError(f"Cannot connect to Ollama at {self.endpoint}. Is Ollama running? (error: {exc})")
        if isinstance(exc, httpx.TimeoutException):
            return EmbeddingError(f"Ollama embedding request timed out: {exc}")
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 404:
                return EmbeddingError(
                    f"Embedding model {self._model!r} not found. Pull it first: ollama pull {self._model}"
                )
            return EmbeddingError(f"Ollama rejected the embedding request (HTTP {status}): {exc}")
        return EmbeddingError(f"Ollama embedding failed: {exc}")

    async def _embed_many(self, inputs: str | list[str]) -> list[list[float]]:
        payload: _EmbedPayload = {"model": self._model, "input": inputs}
        try:
            response = await self._client.post(f"{self.endpoint}/api/embed", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise self._translate(exc) from exc

        data = response.json()
        embeddings: list[list[float]] = data.get("embeddings") or []
        for vec in embeddings:
            if len(vec) != self._dimensions:
                raise EmbeddingError(
                    f"Ollama returned a {len(vec)}-dim vector but this adapter declares "
                    f"{self._dimensions} for {self._model!r}. Check the model/dimensions config."
                )
        return [[float(x) for x in vec] for vec in embeddings]

    async def embed(self, text: str) -> list[float]:
        vectors = await self._embed_many(text)
        if not vectors:
            raise EmbeddingError("Ollama returned no embedding for the input text.")
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._embed_many(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} inputs.")
        return vectors

    async def health_check(self) -> AdapterHealth:
        try:
            response = await self._client.get(f"{self.endpoint}/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            available = self._model in models or any(m.startswith(self._model.split(":")[0]) for m in models)
            return AdapterHealth(
                adapter_id=self._model,
                healthy=available,
                message=("" if available else f"Model {self._model!r} not pulled. Available: {', '.join(models)}"),
            )
        except Exception as exc:
            return AdapterHealth(adapter_id=self._model, healthy=False, message=str(exc))
