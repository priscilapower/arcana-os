"""FastEmbedEmbeddingAdapter — in-process embeddings via fastembed (ONNX Runtime).

fastembed is an optional dependency (``arcana-core[embed]``). It is imported
lazily — never at module load — so this module is safe to import even when the
extra is absent. In that case ``health_check`` reports unhealthy and the caller
moves on; only ``embed``/``ensure_model`` raise if actually invoked without it.
"""

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from arcana.models.adapters.embedding import EmbeddingAdapter, EmbeddingError
from arcana.types import AdapterHealth

_DEFAULT_MODEL_NAME = "nomic-embed-text-v1.5"
_DEFAULT_REPO_ID = "nomic-ai/nomic-embed-text-v1.5"
_DEFAULT_DIMENSIONS = 768
_INSTALL_HINT = "Install arcana-core[embed] to use FastEmbedEmbeddingAdapter (fastembed + ONNX Runtime)."

# Shares a vector space with Ollama's nomic-embed-text; reports the same family so
# either can serve a database the other pinned. Must match the Ollama adapter's value.
_NOMIC_FAMILY = "nomic-text-v1.5"


def _default_cache_dir() -> Path:
    return Path.home() / ".arcana" / "models"


class FastEmbedEmbeddingAdapter(EmbeddingAdapter):
    """In-process embeddings via fastembed's ONNX runtime.

    Defaults to ``nomic-embed-text-v1.5`` (768-dimensional), the same model
    family as the Ollama tier, so vectors are dimension-compatible. The ONNX
    model (~130 MB) downloads to ``~/.arcana/models/`` on first use.
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL_NAME,
        repo_id: str = _DEFAULT_REPO_ID,
        dimensions: int = _DEFAULT_DIMENSIONS,
        cache_dir: Path | None = None,
    ) -> None:
        self._model_name = model_name
        self._repo_id = repo_id
        self._dimensions = dimensions
        self._cache_dir = cache_dir or _default_cache_dir()
        self._backend: Any = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_family(self) -> str:
        return _NOMIC_FAMILY if self._model_name == _DEFAULT_MODEL_NAME else self._model_name

    @staticmethod
    def _package_available() -> bool:
        return importlib.util.find_spec("fastembed") is not None

    def _build_backend(self) -> Any:
        """Import fastembed and construct the model. Blocks on first download."""
        try:
            from fastembed import TextEmbedding  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(_INSTALL_HINT) from exc
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return TextEmbedding(model_name=self._repo_id, cache_dir=str(self._cache_dir))  # pyright: ignore[reportUnknownVariableType]

    async def _ensure_backend(self) -> Any:
        if self._backend is None:
            self._backend = await asyncio.to_thread(self._build_backend)
        return self._backend

    async def ensure_model(self) -> None:
        await self._ensure_backend()

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        backend = await self._ensure_backend()
        try:
            raw = await asyncio.to_thread(lambda: list(backend.embed(texts)))
        except Exception as exc:
            raise EmbeddingError(f"fastembed embedding failed: {exc}") from exc

        vectors: list[list[float]] = []
        for vec in raw:
            out = [float(x) for x in vec]
            if len(out) != self._dimensions:
                raise EmbeddingError(
                    f"fastembed returned a {len(out)}-dim vector but this adapter declares "
                    f"{self._dimensions} for {self._model_name!r}. Check the model/dimensions config."
                )
            vectors.append(out)
        return vectors

    async def embed(self, text: str) -> list[float]:
        vectors = await self._embed_many([text])
        if not vectors:
            raise EmbeddingError("fastembed returned no embedding for the input text.")
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await self._embed_many(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(f"fastembed returned {len(vectors)} embeddings for {len(texts)} inputs.")
        return vectors

    async def health_check(self) -> AdapterHealth:
        if not self._package_available():
            return AdapterHealth(
                adapter_id=self._model_name,
                healthy=False,
                message=f"fastembed not installed. {_INSTALL_HINT}",
            )
        return AdapterHealth(adapter_id=self._model_name, healthy=True)
