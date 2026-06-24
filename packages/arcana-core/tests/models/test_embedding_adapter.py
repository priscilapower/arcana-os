"""Contract tests for the EmbeddingAdapter ABC.

No provider, no network — these exercise the abstract contract against an
in-file fake so the invariants every concrete adapter must uphold are pinned.
"""

import pytest

from arcana.models.adapters.embedding import EmbeddingAdapter, EmbeddingError
from arcana.types import AdapterHealth


class FakeEmbeddingAdapter(EmbeddingAdapter):
    """Minimal concrete adapter: vector encodes text length, padded to width."""

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy
        self.ensure_calls = 0

    @property
    def model_name(self) -> str:
        return "fake-embed"

    @property
    def dimensions(self) -> int:
        return 4

    async def embed(self, text: str) -> list[float]:
        if not text:
            raise EmbeddingError("empty text")
        return [float(len(text)), 0.0, 0.0, 0.0]

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id=self.model_name, healthy=self._healthy)

    async def ensure_model(self) -> None:
        self.ensure_calls += 1


@pytest.fixture
def adapter() -> FakeEmbeddingAdapter:
    return FakeEmbeddingAdapter()


async def test_embed_returns_vector_of_declared_dimensions(adapter: FakeEmbeddingAdapter) -> None:
    vec = await adapter.embed("hello")
    assert len(vec) == adapter.dimensions
    assert all(isinstance(x, float) for x in vec)


async def test_embed_batch_preserves_order_and_length(adapter: FakeEmbeddingAdapter) -> None:
    texts = ["a", "bb", "ccc"]
    batch = await adapter.embed_batch(texts)

    assert len(batch) == len(texts)
    # Default embed_batch must equal serial embed, element for element.
    serial = [await adapter.embed(t) for t in texts]
    assert batch == serial


async def test_embed_raises_embedding_error_on_failure(adapter: FakeEmbeddingAdapter) -> None:
    with pytest.raises(EmbeddingError):
        await adapter.embed("")


async def test_health_check_returns_without_raising() -> None:
    unhealthy = FakeEmbeddingAdapter(healthy=False)
    health = await unhealthy.health_check()
    assert isinstance(health, AdapterHealth)
    assert health.healthy is False
    assert health.adapter_id == "fake-embed"


async def test_ensure_model_default_is_noop() -> None:
    class Bare(EmbeddingAdapter):
        @property
        def model_name(self) -> str:
            return "bare"

        @property
        def dimensions(self) -> int:
            return 1

        async def embed(self, text: str) -> list[float]:
            del text
            return [0.0]

        async def health_check(self) -> AdapterHealth:
            return AdapterHealth(adapter_id="bare", healthy=True)

    # The ABC's default ensure_model is an awaitable no-op returning None.
    assert await Bare().ensure_model() is None


def test_missing_abstract_member_cannot_instantiate() -> None:
    class MissingEmbed(EmbeddingAdapter):
        @property
        def model_name(self) -> str:
            return "x"

        @property
        def dimensions(self) -> int:
            return 1

        async def health_check(self) -> AdapterHealth:
            return AdapterHealth(adapter_id="missing", healthy=True)

    with pytest.raises(TypeError):
        MissingEmbed()  # type: ignore[abstract]
