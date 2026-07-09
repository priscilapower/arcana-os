"""Shared, deterministic test doubles used across the suite.

These let a test drive real code without a model, an embedding provider, or
wall-clock time — and, being defined once, keep the many test modules that need
them from each re-rolling a slightly different copy:

* :func:`make_gateway` — a ``ModelGateway`` mock with canned ``complete``/``stream``.
* :class:`KeywordEmbedder` — a deterministic embedder whose vector counts the
  fixed axis words in the text, so semantic ranking is exact and reproducible.
* :class:`FailingAdapter` — wraps a real ``MemoryAdapter`` and raises on write,
  injecting failure at the behaviour boundary rather than via monkeypatching.
* :class:`StubExtractor` — yields a fixed list of memories and a fixed summary,
  so a test pins exactly what a turn extracts.
"""

from collections.abc import AsyncGenerator, Sequence
from unittest.mock import AsyncMock, MagicMock

from arcana.memory.extraction import heuristic_summary
from arcana.memory.extraction.signals import ENGLISH, SignalPatterns
from arcana.models.adapters.base import CompletionResponse, ModelChunk
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.models.gateway import ModelGateway
from arcana.types import AdapterHealth, MemoryAdapter, MemoryEntry, MemoryQuery, Session

#: Axis words a :class:`KeywordEmbedder` counts into each vector dimension.
AXES = ("alpha", "beta", "gamma", "delta")


def make_gateway(
    *,
    content: str = "Hello from the agent.",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> MagicMock:
    """A ``ModelGateway`` mock: ``complete`` returns *content*, ``stream`` yields it word-by-word.

    Tokens are reported once, on the final streamed chunk, so streaming totals
    match the single-shot ``complete`` response.
    """
    gateway = MagicMock(spec=ModelGateway)
    gateway.complete = AsyncMock(
        return_value=CompletionResponse(content=content, input_tokens=input_tokens, output_tokens=output_tokens)
    )

    words = content.split()

    async def _stream(_model: str, _req: object) -> AsyncGenerator[ModelChunk, None]:
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            yield ModelChunk(
                text=word + " ",
                input_tokens=input_tokens if is_last else 0,
                output_tokens=output_tokens if is_last else 0,
            )

    gateway.stream = _stream
    return gateway


class KeywordEmbedder(EmbeddingAdapter):
    """Deterministic embedder: ``vector[i] = count of AXES[i] in the text``.

    Configurable ``name``/``family``/``healthy``/``dims`` so one class drives the
    pinning, family-fallback, dimension-guard, and health-fallback paths. The
    vector is padded/truncated to ``dims`` and a zero vector is nudged (cosine
    distance is undefined for it).
    """

    def __init__(
        self,
        *,
        name: str = "kw-embed",
        family: str | None = None,
        healthy: bool = True,
        dims: int = len(AXES),
    ) -> None:
        self._name = name
        self._family = family or name
        self._healthy = healthy
        self._dims = dims
        self.embed_calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return self._dims

    @property
    def model_family(self) -> str:
        return self._family

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        low = text.lower()
        vec = [float(low.count(ax)) for ax in AXES]
        vec = (vec + [0.0] * self._dims)[: self._dims]
        if not any(vec):
            vec[0] = 1.0
        return vec

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(adapter_id=self._name, healthy=self._healthy)


class FailingAdapter:
    """Wrap a real ``MemoryAdapter`` and raise on write after ``fail_after`` writes.

    Reads, health, and close delegate to the inner backend unchanged, so a test
    can still inspect the real store's state after an injected failure.
    ``fail_after=0`` (the default) fails every write; ``fail_after=1`` lets the
    first write land and fails the second, and so on.
    """

    def __init__(self, inner: MemoryAdapter, *, fail_after: int = 0) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self.writes = 0

    async def write(self, entry: MemoryEntry) -> None:
        self.writes += 1
        if self.writes > self._fail_after:
            raise RuntimeError(f"injected write failure (write #{self.writes})")
        await self._inner.write(entry)

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        return await self._inner.search(query)

    async def health_check(self) -> AdapterHealth:
        return await self._inner.health_check()

    async def aclose(self) -> None:
        # ``aclose`` is not part of the MemoryAdapter protocol; reach for it only
        # when the wrapped backend has one (a real SQLite store does).
        closer = getattr(self._inner, "aclose", None)
        if closer is not None:
            await closer()


class StubExtractor:
    """A ``MemoryExtractor`` that yields fixed entries and a fixed summary.

    Lets a test pin exactly what a turn extracts — a single sub-threshold entry,
    a promotion-eligible entry, and so on — without depending on the heuristic's
    language cues. ``summary`` defaults to the deterministic model-free summary so
    the close path still consolidates something.
    """

    def __init__(
        self,
        entries: Sequence[MemoryEntry] = (),
        *,
        summary: str | None = None,
        signals: SignalPatterns = ENGLISH,
    ) -> None:
        self._entries = list(entries)
        self._summary = summary
        self._signals = signals

    @property
    def signals(self) -> SignalPatterns:
        return self._signals

    async def extract(self, _prompt: str, _response: str, _session: Session) -> list[MemoryEntry]:
        # Deep copies per call so a run's access-tracking mutations don't leak
        # back into the fixture's template entries.
        return [e.model_copy(deep=True) for e in self._entries]

    async def summarise(self, session: Session) -> str:
        if self._summary is not None:
            return self._summary
        return heuristic_summary(session.messages)
