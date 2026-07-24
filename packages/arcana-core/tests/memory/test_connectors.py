"""Tests for KnowledgeConnectorStore + connector_adapter. No LLM."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import KnowledgeConnectorStore, connector_adapter
from arcana.memory.config import MemoryResilienceConfig
from arcana.memory.errors import MemoryStorageError
from arcana.types import KnowledgeConnector, KnowledgeConnectorKind


def _connector(path: Path, *, name: str = "team") -> KnowledgeConnector:
    return KnowledgeConnector(name=name, kind=KnowledgeConnectorKind.OBSIDIAN, path=str(path))


def _store(tmp_path: Path) -> KnowledgeConnectorStore:
    return KnowledgeConnectorStore(tmp_path / "connections" / "memory-adapters.json")


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_missing_file_is_an_empty_registry(tmp_path: Path):
    store = _store(tmp_path)
    assert store.list() == []
    assert store.get("nope") is None


def test_add_persists_across_instances(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _store(tmp_path).add(_connector(vault))

    reloaded = _store(tmp_path)
    reloaded.load()
    names = [c.name for c in reloaded.list()]
    assert names == ["team"]
    assert reloaded.get("team") is not None


def test_add_stores_only_a_path_reference_not_contents(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "secret.md").write_text("classified content")
    _store(tmp_path).add(_connector(vault))

    on_disk = (tmp_path / "connections" / "memory-adapters.json").read_text()
    assert "classified content" not in on_disk
    assert str(vault) in on_disk


def test_add_replaces_by_name(tmp_path: Path):
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    v1.mkdir()
    v2.mkdir()
    store = _store(tmp_path)
    store.add(_connector(v1, name="team"))
    store.add(_connector(v2, name="team"))
    assert len(store.list()) == 1
    assert store.get("team").path == str(v2)


def test_remove_reports_existence_and_never_touches_folder(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("keep me")
    store = _store(tmp_path)
    store.add(_connector(vault))

    assert store.remove("team") is True
    assert store.remove("team") is False
    assert (vault / "note.md").exists()  # the folder is untouched


def test_corrupt_registry_raises(tmp_path: Path):
    path = tmp_path / "connections" / "memory-adapters.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    with pytest.raises(MemoryStorageError):
        _store(tmp_path).load()


def test_writes_dict_shaped_file(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _store(tmp_path).add(_connector(vault))
    raw = json.loads((tmp_path / "connections" / "memory-adapters.json").read_text())
    assert "connectors" in raw and isinstance(raw["connectors"], list)


def test_add_preserves_sibling_resilience_config(tmp_path: Path):
    """memory-adapters.json is shared with MemoryResilienceConfig — a connector
    write must round-trip the resilience keys untouched (and vice versa)."""
    path = tmp_path / "connections" / "memory-adapters.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"private": {"read_timeout_ms": 111}, "global": {"read_timeout_ms": 222}}))

    vault = tmp_path / "vault"
    vault.mkdir()
    store = _store(tmp_path)
    store.add(_connector(vault))

    raw = json.loads(path.read_text())
    assert raw["private"] == {"read_timeout_ms": 111}
    assert raw["global"] == {"read_timeout_ms": 222}
    assert [c["name"] for c in raw["connectors"]] == ["team"]
    # MemoryResilienceConfig still reads its keys after the connector write.
    rc = MemoryResilienceConfig.load(path)
    assert rc.private.read_timeout_ms == 111
    assert rc.global_.read_timeout_ms == 222

    # Removing the connector leaves the resilience keys in place.
    store.remove("team")
    raw2 = json.loads(path.read_text())
    assert raw2["private"] == {"read_timeout_ms": 111}
    assert raw2["connectors"] == []


# --------------------------------------------------------------------------
# connector_adapter
# --------------------------------------------------------------------------


async def test_connector_adapter_is_healthy_and_reads_notes(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("first note")
    (vault / "b.md").write_text("second note")

    adapter = connector_adapter(_connector(vault), uuid4())
    health = await adapter.health_check()
    assert health.healthy is True
    assert len(await adapter.scan()) == 2


async def test_connector_adapter_unreachable_vault_is_unhealthy_not_crash(tmp_path: Path):
    adapter = connector_adapter(_connector(tmp_path / "gone"), uuid4())
    health = await adapter.health_check()
    assert health.healthy is False


def test_connector_adapter_is_read_only(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    adapter = connector_adapter(_connector(vault, name="team"), uuid4())
    assert adapter.CAPABILITIES.is_writable is False
