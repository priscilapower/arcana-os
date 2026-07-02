"""Latency calibration bench for memory tier timeout budgets.

Measures p50/p99 read latency for keyword (FTS5) vs semantic (sqlite-vec) search,
so the provisional per-tier timeout budgets in ``TierResilienceConfig`` can be
replaced with measured numbers rather than guesses.

Marked ``llm_eval`` — the semantic leg needs a live embedding backend (fastembed
in-process, or Ollama), so it is excluded from the default CI lane. Run manually:

    uv run pytest packages/arcana-core/tests/memory/test_latency_bench.py -m llm_eval -s -o addopts=""

The keyword leg always runs; the semantic leg is skipped (with a note) when no
embedding backend is healthy.
"""

import time
from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import SQLiteAdapter, TierResilienceConfig
from arcana.memory.adapters.vector import VectorAdapter
from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.models.adapters.embedding import EmbeddingAdapter
from arcana.models.adapters.fastembed_embedding import FastEmbedEmbeddingAdapter
from arcana.models.adapters.ollama_embedding import OllamaEmbeddingAdapter
from arcana.types import MemoryEntry, MemoryQuery, MemoryType, RetrievalMode

pytestmark = pytest.mark.llm_eval

_N_ENTRIES = 500
_N_QUERIES = 200
_TOPICS = ["kitchen", "travel", "budget", "health", "music", "garden", "coding", "coffee"]


def _percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def _healthy_embedder() -> EmbeddingAdapter | None:
    for candidate in (FastEmbedEmbeddingAdapter(), OllamaEmbeddingAdapter()):
        try:
            if (await candidate.health_check()).healthy:
                return candidate
        except Exception:  # noqa: BLE001 — probing, any failure just means "not this one"
            continue
    return None


async def _bench(adapter: SQLiteAdapter | VectorAdapter, mode: RetrievalMode) -> tuple[float, float]:
    samples: list[float] = []
    for i in range(_N_QUERIES):
        query = MemoryQuery(text=_TOPICS[i % len(_TOPICS)], retrieval_mode=mode, limit=10)
        start = time.perf_counter()
        await adapter.search(query)
        samples.append((time.perf_counter() - start) * 1000)
    return _percentile(samples, 0.50), _percentile(samples, 0.99)


def _recommend(label: str, p99: float, default_ms: int) -> str:
    headroom = default_ms / p99 if p99 > 0 else float("inf")
    if p99 > default_ms:
        verdict = f"⚠ p99 EXCEEDS default {default_ms}ms — raise the {label} budget"
    elif headroom < 2:
        verdict = f"tight — p99 is {headroom:.1f}× under the {default_ms}ms default"
    else:
        verdict = f"ok — {headroom:.1f}× headroom under the {default_ms}ms default"
    return verdict


async def test_read_latency_bench(tmp_path: Path):
    defaults = TierResilienceConfig()
    sqlite = SQLiteAdapter(tmp_path / "bench.db")
    await sqlite.connect()

    embedder = await _healthy_embedder()
    adapter: SQLiteAdapter | VectorAdapter = (
        VectorAdapter(sqlite, EmbeddingGateway([embedder])) if embedder is not None else sqlite
    )

    for i in range(_N_ENTRIES):
        await adapter.write(
            MemoryEntry(
                agent_id=uuid4(),
                type=MemoryType.SEMANTIC,
                content=f"note {i} about {_TOPICS[i % len(_TOPICS)]} and related things",
                importance=(i % 10) / 10,
            )
        )

    kw_p50, kw_p99 = await _bench(adapter, RetrievalMode.keyword)

    print(f"\n--- memory read latency bench (n={_N_ENTRIES} entries, {_N_QUERIES} queries) ---")
    kw_verdict = _recommend("read", kw_p99, defaults.read_timeout_ms)
    print(f"keyword  : p50={kw_p50:6.2f}ms  p99={kw_p99:6.2f}ms  | {kw_verdict}")
    assert kw_p99 >= 0

    if embedder is None:
        print("semantic : SKIPPED — no healthy embedding backend (install arcana-os[vector] or run Ollama)")
        pytest.skip("no embedding backend available for the semantic leg")

    sem_p50, sem_p99 = await _bench(adapter, RetrievalMode.semantic)
    print(
        f"semantic : p50={sem_p50:6.2f}ms  p99={sem_p99:6.2f}ms  "
        f"| {_recommend('semantic', sem_p99, defaults.semantic_timeout_ms)}  ({embedder.model_name})"
    )
    print("Feed the p99s back into TierResilienceConfig / memory-adapters.json defaults.")
    assert sem_p99 >= 0
