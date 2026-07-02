"""Integration tests: MarkdownFolderAdapter inside a real MemoryFederation.

The unit suite exercises the adapter in isolation; these wire it into the live
stack — ``MarkdownFolderAdapter`` registered as a shared read tier alongside a real
``SQLiteAdapter`` private tier, behind a ``MemoryRouter`` and ``MemoryFederation`` —
and assert the design-ahead claims about the folder connector: merged reads dedup + rank
a folder tier against SQLite, the stable ``uuid5`` path id makes that dedup work across
tiers, and the read-only connector stays safe on the federation's write and prune
paths. No LLM calls.
"""

from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import (
    MarkdownFolderAdapter,
    MemoryFederation,
    MemoryRouter,
    SQLiteAdapter,
)
from arcana.memory.errors import MemoryWriteError
from arcana.observability import MemoryDegradedEvent
from arcana.types import (
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    PruneMode,
    PrunePolicy,
    RetrievalMode,
)

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


def _write_note(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


async def _sqlite_tier(db_path: Path) -> SQLiteAdapter:
    adapter = SQLiteAdapter(db_path)
    await adapter.connect()
    return adapter


@pytest.fixture
async def vault(tmp_path: Path):
    """A small Markdown vault: one pinned note, one plain note, both mention alpha."""
    root = tmp_path / "vault"
    _write_note(root, "pinned.md", "---\npinned: true\nimportance: 0.3\n---\nalpha pinned note")
    _write_note(root, "plain.md", "---\nimportance: 0.4\n---\nalpha plain note")
    return root


# --------------------------------------------------------------------------
# Merged reads: folder tier ranked against SQLite
# --------------------------------------------------------------------------


async def test_folder_tier_merges_and_ranks_with_sqlite(tmp_path: Path, vault: Path):
    agent = uuid4()
    private = await _sqlite_tier(tmp_path / "private.db")
    await private.write(
        MemoryEntry(agent_id=agent, type=MemoryType.SEMANTIC, content="alpha from sqlite", importance=0.5)
    )
    folder = MarkdownFolderAdapter(vault, agent, scope=MemoryScope.SHARED, pool_name="vault")
    fed = MemoryFederation(MemoryRouter(private=private, pools={"vault": folder}))

    got = await fed.search(MemoryQuery(agent_id=agent, text="alpha", retrieval_mode=RetrievalMode.semantic))

    contents = [e.content for e in got]
    # Both tiers contribute; the folder's pinned note sorts first (rank() is
    # pinned-first), and the SQLite row is merged in alongside the folder notes.
    assert len(got) == 3
    assert contents[0] == "alpha pinned note"
    assert "alpha from sqlite" in contents

    await private.aclose()


async def test_folder_entries_carry_shared_scope_through_the_stack(tmp_path: Path, vault: Path):
    agent = uuid4()
    private = await _sqlite_tier(tmp_path / "private.db")
    folder = MarkdownFolderAdapter(vault, agent, scope=MemoryScope.SHARED, pool_name="vault")
    fed = MemoryFederation(MemoryRouter(private=private, pools={"vault": folder}))

    got = await fed.search(MemoryQuery(agent_id=agent, scope=MemoryScope.SHARED, pool_name="vault", text="alpha"))

    assert got  # the named-pool read reaches the folder tier
    assert all(e.scope is MemoryScope.SHARED and e.pool_name == "vault" for e in got)

    await private.aclose()


# --------------------------------------------------------------------------
# Dedup by stable uuid5 path id
# --------------------------------------------------------------------------


async def test_stable_id_dedups_folder_against_prior_ingest(tmp_path: Path, vault: Path):
    """A prior ingest into SQLite shares the folder note's id → merged read is deduped.

    Simulates the roadmap's ingest path: an entry already lives in the private
    SQLite tier under the same ``uuid5`` id the folder derives for that file. A
    federated read must return it once, keeping the most-local (private) copy.
    """
    agent = uuid4()
    folder = MarkdownFolderAdapter(vault, agent, scope=MemoryScope.SHARED, pool_name="vault")

    # Learn the folder's stable id for pinned.md, then seed SQLite with the same id
    # but distinct content, standing in for a previous ingest of that file.
    [pinned] = await folder.search(MemoryQuery(text="pinned", limit=10))
    private = await _sqlite_tier(tmp_path / "private.db")
    await private.write(
        MemoryEntry(
            id=pinned.id,
            agent_id=agent,
            type=MemoryType.SEMANTIC,
            content="alpha ingested copy",
            importance=0.9,
        )
    )
    fed = MemoryFederation(MemoryRouter(private=private, pools={"vault": folder}))

    got = await fed.search(MemoryQuery(agent_id=agent, text="alpha pinned", retrieval_mode=RetrievalMode.hybrid))

    ids = [e.id for e in got]
    assert ids.count(pinned.id) == 1  # deduped across tiers by the shared id
    kept = next(e for e in got if e.id == pinned.id)
    assert kept.content == "alpha ingested copy"  # most-local (private) copy wins

    await private.aclose()


# --------------------------------------------------------------------------
# Read-only safety on federation-wide operations
# --------------------------------------------------------------------------


async def test_prune_fan_out_skips_the_read_only_folder(tmp_path: Path, vault: Path):
    """Federation prune touches the writable SQLite tier only; the folder is skipped."""
    agent = uuid4()
    private = await _sqlite_tier(tmp_path / "private.db")
    await private.write(MemoryEntry(agent_id=agent, type=MemoryType.SEMANTIC, content="low value", importance=0.1))
    folder = MarkdownFolderAdapter(vault, agent, scope=MemoryScope.SHARED, pool_name="vault")
    fed = MemoryFederation(MemoryRouter(private=private, pools={"vault": folder}))

    report = await fed.prune(PrunePolicy(min_importance=0.5, mode=PruneMode.ARCHIVE))

    assert report.tiers == 1  # only the SQLite tier is prunable
    assert report.archived == 1
    # The folder is untouched — both source files still present on disk.
    assert sorted(p.name for p in vault.glob("*.md")) == ["pinned.md", "plain.md"]

    await private.aclose()


async def test_shared_write_to_folder_degrades_not_fatal(tmp_path: Path, vault: Path):
    """A SHARED write routed to the read-only folder degrades; the session proceeds.

    The private write is the durability anchor and succeeds; the folder leg raises
    ``MemoryWriteError``, which the resilience layer surfaces as a degraded SHARED
    tier rather than failing the caller.
    """
    agent = uuid4()
    private = await _sqlite_tier(tmp_path / "private.db")
    folder = MarkdownFolderAdapter(vault, agent, scope=MemoryScope.SHARED, pool_name="vault")

    events: list[MemoryDegradedEvent] = []
    fed = MemoryFederation(MemoryRouter(private=private, pools={"vault": folder}), on_degraded=events.append)

    shared_entry = MemoryEntry(
        agent_id=agent,
        type=MemoryType.SEMANTIC,
        content="alpha shared write",
        scope=MemoryScope.SHARED,
        pool_name="vault",
    )
    await fed.write(shared_entry)  # must not raise

    assert len(events) == 1
    assert events[0].tier == "shared:vault"
    assert events[0].operation == "write"
    # No file was created in the read-only vault.
    assert sorted(p.name for p in vault.glob("*.md")) == ["pinned.md", "plain.md"]

    await private.aclose()


async def test_direct_folder_write_raises(vault: Path):
    """The adapter itself still enforces read-only when addressed directly."""
    folder = MarkdownFolderAdapter(vault, uuid4())
    with pytest.raises(MemoryWriteError):
        await folder.write(MemoryEntry(agent_id=uuid4(), type=MemoryType.SEMANTIC, content="x"))


# --------------------------------------------------------------------------
# Health aggregation
# --------------------------------------------------------------------------


async def test_federation_health_names_a_missing_folder_but_stays_up(tmp_path: Path):
    agent = uuid4()
    private = await _sqlite_tier(tmp_path / "private.db")
    missing = MarkdownFolderAdapter(tmp_path / "gone", agent, scope=MemoryScope.SHARED, pool_name="vault")
    fed = MemoryFederation(MemoryRouter(private=private, pools={"vault": missing}))

    health = await fed.health_check()

    assert health.healthy is True  # SQLite tier keeps the federation usable
    assert "shared:vault" in health.message  # the degraded folder tier is named

    await private.aclose()
