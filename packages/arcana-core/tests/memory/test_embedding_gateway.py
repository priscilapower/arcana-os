"""Tests for EmbeddingGateway resolution + the v3 embedding_meta migration."""

from pathlib import Path

import aiosqlite

from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.memory.migrations import MIGRATIONS, latest_version, migrate_to_latest
from arcana.models.adapters.embedding import AdapterHealth, EmbeddingAdapter
from arcana.models.adapters.fastembed_embedding import FastEmbedEmbeddingAdapter
from arcana.models.adapters.ollama_embedding import OllamaEmbeddingAdapter
from arcana.types import EmbeddingMeta


class FakeAdapter(EmbeddingAdapter):
    """Configurable stand-in: declares a model_name/family and a health verdict."""

    def __init__(self, name: str, *, family: str | None = None, healthy: bool = True) -> None:
        self._name = name
        self._family = family or name
        self._healthy = healthy
        self.ensure_calls = 0
        self.health_calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return 768

    @property
    def model_family(self) -> str:
        return self._family

    async def embed(self, text: str) -> list[float]:
        return [0.0] * 768

    async def health_check(self) -> AdapterHealth:
        self.health_calls += 1
        return AdapterHealth(adapter_id=self._name, healthy=self._healthy)

    async def ensure_model(self) -> None:
        self.ensure_calls += 1


def _meta(model_name: str) -> EmbeddingMeta:
    return EmbeddingMeta(model_name=model_name, dimensions=768)


# --------------------------------------------------------------------------
# Unpinned (new database)
# --------------------------------------------------------------------------


async def test_new_db_pins_to_first_healthy_in_priority_order() -> None:
    first = FakeAdapter("ollama", healthy=False)
    second = FakeAdapter("fastembed", healthy=True)
    gw = EmbeddingGateway([first, second])

    chosen = await gw.resolve(None)

    assert chosen is second
    assert second.ensure_calls == 1  # warmed before use


async def test_new_db_returns_none_when_nothing_healthy() -> None:
    gw = EmbeddingGateway([FakeAdapter("ollama", healthy=False)])
    assert await gw.resolve(None) is None


# --------------------------------------------------------------------------
# Pinned — exact match
# --------------------------------------------------------------------------


async def test_pinned_returns_exact_model_when_healthy() -> None:
    ollama = FakeAdapter("nomic-embed-text", family="nomic-text-v1.5", healthy=True)
    fast = FakeAdapter("nomic-embed-text-v1.5", family="nomic-text-v1.5", healthy=True)
    gw = EmbeddingGateway([ollama, fast])

    chosen = await gw.resolve(_meta("nomic-embed-text"))

    assert chosen is ollama
    assert ollama.ensure_calls == 0  # already in use; not re-warmed


# --------------------------------------------------------------------------
# Pinned — family fallback (the cross-tier behaviour)
# --------------------------------------------------------------------------


async def test_pinned_falls_back_within_family_when_exact_unhealthy() -> None:
    ollama = FakeAdapter("nomic-embed-text", family="nomic-text-v1.5", healthy=False)
    fast = FakeAdapter("nomic-embed-text-v1.5", family="nomic-text-v1.5", healthy=True)
    gw = EmbeddingGateway([ollama, fast])

    chosen = await gw.resolve(_meta("nomic-embed-text"))

    assert chosen is fast  # compatible tier, not FTS5
    assert fast.ensure_calls == 1


async def test_pinned_returns_none_when_no_family_member_healthy() -> None:
    ollama = FakeAdapter("nomic-embed-text", family="nomic-text-v1.5", healthy=False)
    fast = FakeAdapter("nomic-embed-text-v1.5", family="nomic-text-v1.5", healthy=False)
    gw = EmbeddingGateway([ollama, fast])

    assert await gw.resolve(_meta("nomic-embed-text")) is None


async def test_pinned_does_not_substitute_across_families() -> None:
    # Different family → not interchangeable → never silently substituted.
    pinned_down = FakeAdapter("nomic-embed-text", family="nomic-text-v1.5", healthy=False)
    other = FakeAdapter("some-other-model", family="other-family", healthy=True)
    gw = EmbeddingGateway([pinned_down, other])

    assert await gw.resolve(_meta("nomic-embed-text")) is None


async def test_pinned_unknown_model_returns_none() -> None:
    gw = EmbeddingGateway([FakeAdapter("fastembed-only", healthy=True)])
    assert await gw.resolve(_meta("a-model-no-adapter-knows")) is None


async def test_health_checked_at_most_once_per_adapter() -> None:
    ollama = FakeAdapter("nomic-embed-text", family="nomic-text-v1.5", healthy=False)
    fast = FakeAdapter("nomic-embed-text-v1.5", family="nomic-text-v1.5", healthy=True)
    gw = EmbeddingGateway([ollama, fast])

    await gw.resolve(_meta("nomic-embed-text"))

    # ollama is probed in the exact pass and again eligible in the family pass —
    # memoisation must keep it to a single real probe.
    assert ollama.health_calls == 1
    assert fast.health_calls == 1


# --------------------------------------------------------------------------
# Real adapters share a family (drift guard)
# --------------------------------------------------------------------------


def test_ollama_and_fastembed_default_models_share_a_family() -> None:
    assert OllamaEmbeddingAdapter().model_family == FastEmbedEmbeddingAdapter().model_family


def test_non_default_model_reverts_to_its_own_family() -> None:
    # Point an adapter at a different model and it must NOT claim the nomic family.
    assert OllamaEmbeddingAdapter(model="mxbai-embed-large").model_family == "mxbai-embed-large"
    assert FastEmbedEmbeddingAdapter(model_name="BAAI/bge-small-en").model_family == "BAAI/bge-small-en"


# --------------------------------------------------------------------------
# Migration v3 — embedding_meta
# --------------------------------------------------------------------------


async def test_migration_v3_creates_embedding_meta(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "m.db") as conn:
        version = await migrate_to_latest(conn)
        assert version == latest_version() == 3

        row = await (
            await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='embedding_meta'")
        ).fetchone()
        assert row is not None

        cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(embedding_meta)")).fetchall()}
        assert cols == {"model_name", "dimensions", "first_used_at", "entry_count"}


def test_v3_registered_once_in_ascending_order() -> None:
    versions = [v for v, _ in MIGRATIONS]
    assert versions == sorted(versions)
    assert versions.count(3) == 1
