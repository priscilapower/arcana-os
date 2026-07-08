"""Shared, deterministic test doubles for memory integration tests.

These fakes let a test drive the *real* federation stack without a model, an
embedding provider, or wall-clock time:

* :class:`FakeEmbedding` — a hashing embedder (``text → vector``) that is stable
  across processes, so the semantic GLOBAL tier is reproducible without a live
  model. Same-token overlap produces cosine-similar vectors, so ranking is
  meaningful, not random.
* :class:`FailingAdapter` — wraps a real :class:`MemoryAdapter` and raises on the
  Nth write (or every write), so failure is injected at the *behaviour* boundary
  (a tier that stops accepting writes) rather than by monkeypatching internals.
* :class:`StubExtractor` — yields a fixed list of :class:`MemoryEntry` per turn
  and a fixed summary, so a test controls exactly what extraction produces
  (a sub-threshold entry, a promotion-eligible entry, …).

They live here, under ``tests/memory``, so both the federation suite and the
agent-integration suite construct memory from one source of truth.
"""

import hashlib
from collections.abc import Sequence

from arcana.memory.extraction import heuristic_summary
from arcana.memory.extraction.signals import ENGLISH, SignalPatterns
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.types import (
    AdapterHealth,
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    Session,
)


class FakeEmbedding(EmbeddingAdapter):
    """Deterministic hashing embedder — no model, stable across processes.

    Each whitespace token is hashed (SHA-256, *not* the salted built-in ``hash``)
    into one of ``dimensions`` buckets and counted, so two texts that share tokens
    land near each other in vector space while unrelated texts stay apart. A
    zero vector (no tokens) is nudged so cosine distance stays defined.
    """

    def __init__(
        self,
        *,
        name: str = "fake-embed",
        family: str | None = None,
        dimensions: int = 16,
        healthy: bool = True,
    ) -> None:
        self._name = name
        self._family = family or name
        self._dimensions = dimensions
        self._healthy = healthy
        self.embed_calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_family(self) -> str:
        return self._family

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        vec = [0.0] * self._dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimensions
            vec[bucket] += 1.0
        if not any(vec):
            vec[0] = 1.0  # cosine distance is undefined for a zero vector
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
    """A :class:`MemoryExtractor` that yields fixed entries and a fixed summary.

    Lets a test pin exactly what a turn extracts — a single sub-threshold entry
    (to prove the store-confidence filter drops it), a promotion-eligible entry
    (to exercise the GLOBAL tier), and so on — without depending on the
    heuristic's language cues. ``summary`` defaults to the deterministic
    model-free summary so the close path still consolidates something.
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
