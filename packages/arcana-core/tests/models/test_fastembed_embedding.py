"""Tests for FastEmbedEmbeddingAdapter.

fastembed is an optional dep that may not be installed in CI, so the backend
and the availability probe are both mocked — no ONNX, no model download.
"""

from collections.abc import Iterable, Iterator

import pytest

from arcana.models.adapters.fastembed_embedding import (
    EmbeddingError,
    FastEmbedEmbeddingAdapter,
)
from arcana.types import AdapterHealth


class _FakeBackend:
    """Stand-in for fastembed.TextEmbedding: yields fixed-width vectors."""

    def __init__(self, width: int = 3) -> None:
        self.width = width
        self.calls: list[list[str]] = []

    def embed(self, texts: Iterable[str]) -> Iterator[list[float]]:
        batch = list(texts)
        self.calls.append(batch)
        for _ in batch:
            yield [0.1] * self.width


@pytest.fixture
def adapter(tmp_path):
    return FastEmbedEmbeddingAdapter(dimensions=3, cache_dir=tmp_path)


def _with_backend(adapter: FastEmbedEmbeddingAdapter, backend: _FakeBackend) -> None:
    """Wire a fake backend so _build_backend is never called (no real import)."""
    adapter._backend = backend


async def test_model_name_and_dimensions(adapter) -> None:
    assert adapter.model_name == "nomic-embed-text-v1.5"
    assert adapter.dimensions == 3


async def test_embed_returns_single_vector(adapter) -> None:
    _with_backend(adapter, _FakeBackend(width=3))
    vec = await adapter.embed("hello")
    assert vec == [0.1, 0.1, 0.1]


async def test_embed_batch_calls_backend_once(adapter) -> None:
    backend = _FakeBackend(width=3)
    _with_backend(adapter, backend)

    out = await adapter.embed_batch(["a", "b"])

    assert out == [[0.1, 0.1, 0.1], [0.1, 0.1, 0.1]]
    assert backend.calls == [["a", "b"]]  # one batched call


async def test_embed_batch_empty_short_circuits(adapter) -> None:
    backend = _FakeBackend()
    _with_backend(adapter, backend)
    assert await adapter.embed_batch([]) == []
    assert backend.calls == []


async def test_dimension_mismatch_raises(adapter) -> None:
    _with_backend(adapter, _FakeBackend(width=4))  # 4 != declared 3
    with pytest.raises(EmbeddingError, match="dim"):
        await adapter.embed("hello")


async def test_backend_failure_becomes_embedding_error(adapter) -> None:
    class _Boom:
        def embed(self, texts):
            raise RuntimeError("onnx exploded")

    _with_backend(adapter, _Boom())
    with pytest.raises(EmbeddingError, match="fastembed embedding failed"):
        await adapter.embed("hello")


async def test_ensure_model_builds_backend_once(adapter, monkeypatch) -> None:
    backend = _FakeBackend()
    calls = {"n": 0}

    def fake_build() -> _FakeBackend:
        calls["n"] += 1
        return backend

    monkeypatch.setattr(adapter, "_build_backend", fake_build)

    await adapter.ensure_model()
    await adapter.ensure_model()
    await adapter.embed("x")

    assert calls["n"] == 1  # built once, reused


async def test_missing_package_propagates_import_error(adapter, monkeypatch) -> None:
    def boom() -> object:
        raise ImportError("Install arcana-core[embed] ...")

    monkeypatch.setattr(adapter, "_build_backend", boom)
    with pytest.raises(ImportError, match=r"arcana-core\[embed\]"):
        await adapter.embed("hello")


async def test_health_check_unhealthy_when_package_missing(adapter, monkeypatch) -> None:
    monkeypatch.setattr(adapter, "_package_available", lambda: False)

    health = await adapter.health_check()

    assert isinstance(health, AdapterHealth)
    assert health.healthy is False
    assert "arcana-core[embed]" in health.message
    assert health.adapter_id == "nomic-embed-text-v1.5"


async def test_health_check_healthy_when_package_present(adapter, monkeypatch) -> None:
    monkeypatch.setattr(adapter, "_package_available", lambda: True)

    health = await adapter.health_check()

    assert health.healthy is True


def test_module_imports_without_fastembed_installed() -> None:
    # The whole point of lazy import: importing the module/class never needs the extra.
    from arcana.models.adapters import FastEmbedEmbeddingAdapter as _Exported

    assert _Exported is FastEmbedEmbeddingAdapter
