"""Unit tests for MarkdownFolderAdapter. No LLM calls, tmp_path-backed."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from arcana.memory import MarkdownFolderAdapter
from arcana.memory.errors import MemoryWriteError
from arcana.types import (
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetrievalMode,
)

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
def agent_id() -> UUID:
    return uuid4()


@pytest.fixture
def adapter(tmp_path: Path, agent_id):
    return MarkdownFolderAdapter(tmp_path, agent_id)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# File -> entry mapping
# --------------------------------------------------------------------------


async def test_frontmatter_maps_all_fields(tmp_path: Path, agent_id):
    _write(
        tmp_path,
        "note.md",
        "---\ntype: procedural\nimportance: 0.82\npinned: true\ntags: [alpha, beta]\n---\nBody text here.\n",
    )
    adapter = MarkdownFolderAdapter(tmp_path, agent_id)
    [entry] = await adapter.search(MemoryQuery(limit=10))

    assert entry.type is MemoryType.PROCEDURAL
    assert entry.importance == pytest.approx(0.82)
    assert entry.pinned is True
    assert entry.tags == ["alpha", "beta"]
    assert entry.agent_id == agent_id
    assert entry.scope is MemoryScope.PRIVATE
    assert entry.embedding is None
    assert entry.confidence == 1.0


async def test_frontmatter_stripped_from_content(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "n.md", "---\ntype: semantic\n---\nOnly the body survives.\n")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.content == "Only the body survives.\n"
    assert "type:" not in entry.content


async def test_defaults_when_no_frontmatter(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "plain.md", "no frontmatter at all")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.type is MemoryType.SEMANTIC  # folder notes are reference knowledge
    assert entry.importance == 0.5
    assert entry.pinned is False
    assert entry.content == "no frontmatter at all"


async def test_default_type_is_configurable(tmp_path: Path, agent_id):
    _write(tmp_path, "n.md", "body")
    adapter = MarkdownFolderAdapter(tmp_path, agent_id, default_type=MemoryType.EPISODIC)
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.type is MemoryType.EPISODIC


async def test_invalid_type_falls_back_to_default(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "n.md", "---\ntype: nonsense\n---\nbody")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.type is MemoryType.SEMANTIC


async def test_importance_clamped(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "hi.md", "---\nimportance: 5\n---\nbody")
    _write(tmp_path, "lo.md", "---\nimportance: -3\n---\nbody")
    entries = await adapter.search(MemoryQuery(limit=10))
    assert sorted(e.importance for e in entries) == [0.0, 1.0]


async def test_tags_from_comma_string_and_hashtags(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "n.md", "---\ntags: work, urgent\n---\nSee #project and #project again and #done.")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.tags == ["work", "urgent", "project", "done"]  # union, order-preserving, deduped


async def test_hashtag_not_confused_with_heading(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "n.md", "# Heading Is Not A Tag\n\nBut #realtag is.")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.tags == ["realtag"]


async def test_block_list_tags(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "n.md", "---\ntags:\n  - one\n  - two\n---\nbody")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    assert entry.tags == ["one", "two"]


async def test_malformed_frontmatter_tolerated(adapter: MarkdownFolderAdapter, tmp_path: Path):
    # Opens a --- block but never closes it -> treat whole file as body, never raise.
    _write(tmp_path, "n.md", "---\ntype: semantic\nthis never closes\nbody continues")
    entries = await adapter.search(MemoryQuery(limit=10))
    assert len(entries) == 1
    assert "this never closes" in entries[0].content


async def test_timestamps_from_file_stat(adapter: MarkdownFolderAdapter, tmp_path: Path):
    path = _write(tmp_path, "n.md", "body")
    [entry] = await adapter.search(MemoryQuery(limit=10))
    st = path.stat()
    assert entry.created_at == datetime.fromtimestamp(st.st_ctime, tz=UTC)
    assert entry.last_accessed_at == datetime.fromtimestamp(st.st_mtime, tz=UTC)


# --------------------------------------------------------------------------
# Stable id
# --------------------------------------------------------------------------


async def test_stable_id_across_constructions(tmp_path: Path, agent_id):
    _write(tmp_path, "sub/note.md", "body")
    a1 = MarkdownFolderAdapter(tmp_path, agent_id)
    a2 = MarkdownFolderAdapter(tmp_path, agent_id)
    [e1] = await a1.search(MemoryQuery(limit=10))
    [e2] = await a2.search(MemoryQuery(limit=10))
    assert e1.id == e2.id  # uuid5 over relative path is idempotent


async def test_rename_changes_id(tmp_path: Path, agent_id):
    _write(tmp_path, "old.md", "body")
    a = MarkdownFolderAdapter(tmp_path, agent_id)
    [before] = await a.search(MemoryQuery(limit=10))
    (tmp_path / "old.md").rename(tmp_path / "new.md")
    [after] = await a.search(MemoryQuery(limit=10))
    assert before.id != after.id


# --------------------------------------------------------------------------
# search — filtering & ranking
# --------------------------------------------------------------------------


async def test_empty_folder_returns_empty(adapter: MarkdownFolderAdapter):
    assert await adapter.search(MemoryQuery(text="anything", limit=10)) == []


async def test_keyword_filter_over_content(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "a.md", "the quick brown fox")
    _write(tmp_path, "b.md", "lazy dogs sleep")
    results = await adapter.search(MemoryQuery(text="fox", limit=10))
    assert [e.content for e in results] == ["the quick brown fox"]


async def test_keyword_matches_filename_and_tags(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "budget-plan.md", "generic body")  # match via filename
    _write(tmp_path, "other.md", "---\ntags: [budget]\n---\nbody")  # match via tag
    results = await adapter.search(MemoryQuery(text="budget", limit=10))
    assert len(results) == 2


async def test_rank_pinned_then_relevance_then_importance(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "pinned.md", "---\npinned: true\nimportance: 0.1\n---\napple")
    _write(tmp_path, "relevant.md", "---\nimportance: 0.2\n---\napple apple apple")
    _write(tmp_path, "important.md", "---\nimportance: 0.99\n---\napple")
    results = await adapter.search(MemoryQuery(text="apple", limit=10))
    assert results[0].pinned  # pinned wins regardless of low relevance/importance
    assert results[1].content == "apple apple apple"  # then lexical relevance
    assert results[2].importance == pytest.approx(0.99)  # then importance


async def test_limit_honoured(adapter: MarkdownFolderAdapter, tmp_path: Path):
    for i in range(5):
        _write(tmp_path, f"n{i}.md", "shared token")
    results = await adapter.search(MemoryQuery(text="token", limit=2))
    assert len(results) == 2


async def test_type_filter(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "a.md", "---\ntype: episodic\n---\nbody")
    _write(tmp_path, "b.md", "---\ntype: semantic\n---\nbody")
    results = await adapter.search(MemoryQuery(type=MemoryType.EPISODIC, limit=10))
    assert len(results) == 1 and results[0].type is MemoryType.EPISODIC


async def test_tags_filter_requires_all(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "a.md", "---\ntags: [x, y]\n---\nbody")
    _write(tmp_path, "b.md", "---\ntags: [x]\n---\nbody")
    results = await adapter.search(MemoryQuery(tags=["x", "y"], limit=10))
    assert len(results) == 1 and set(results[0].tags) >= {"x", "y"}


async def test_time_range_filter_on_last_accessed(adapter: MarkdownFolderAdapter, tmp_path: Path):
    old = _write(tmp_path, "old.md", "old body")
    _write(tmp_path, "new.md", "new body")
    old_mtime = (datetime.now(tz=UTC) - timedelta(days=10)).timestamp()
    os.utime(old, (old_mtime, old_mtime))
    cutoff = datetime.now(tz=UTC) - timedelta(days=1)
    results = await adapter.search(MemoryQuery(time_from=cutoff, limit=10))
    assert [e.content for e in results] == ["new body"]  # the old note falls outside the window


async def test_min_importance_filter(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "hi.md", "---\nimportance: 0.8\n---\nbody")
    _write(tmp_path, "lo.md", "---\nimportance: 0.2\n---\nbody")
    results = await adapter.search(MemoryQuery(min_importance=0.5, limit=10))
    assert len(results) == 1 and results[0].importance == pytest.approx(0.8)


async def test_scope_mismatch_returns_empty(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "n.md", "body")
    # Adapter is PRIVATE by default; a GLOBAL-scoped query cannot match.
    assert await adapter.search(MemoryQuery(scope=MemoryScope.GLOBAL, limit=10)) == []
    assert len(await adapter.search(MemoryQuery(scope=MemoryScope.PRIVATE, limit=10))) == 1


# --------------------------------------------------------------------------
# Retrieval-mode collapse
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [RetrievalMode.semantic, RetrievalMode.hybrid])
async def test_semantic_and_hybrid_collapse_to_keyword(adapter: MarkdownFolderAdapter, tmp_path: Path, mode):
    _write(tmp_path, "n.md", "searchable body")
    results = await adapter.search(MemoryQuery(text="searchable", retrieval_mode=mode, limit=10))
    assert [e.content for e in results] == ["searchable body"]  # served, not errored


# --------------------------------------------------------------------------
# Index cache
# --------------------------------------------------------------------------


async def test_cache_picks_up_new_file(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "a.md", "first")
    assert len(await adapter.search(MemoryQuery(limit=10))) == 1
    _write(tmp_path, "b.md", "second")
    assert len(await adapter.search(MemoryQuery(limit=10))) == 2


async def test_cache_reparses_on_modification(adapter: MarkdownFolderAdapter, tmp_path: Path):
    path = _write(tmp_path, "a.md", "original content")
    [before] = await adapter.search(MemoryQuery(limit=10))
    assert before.content == "original content"
    # Bump mtime forward so the change is detectable even on coarse clocks.
    path.write_text("updated content", encoding="utf-8")
    future = datetime.now(tz=UTC).timestamp() + 5
    os.utime(path, (future, future))
    [after] = await adapter.search(MemoryQuery(limit=10))
    assert after.content == "updated content"


async def test_cache_drops_deleted_file(adapter: MarkdownFolderAdapter, tmp_path: Path):
    path = _write(tmp_path, "a.md", "body")
    assert len(await adapter.search(MemoryQuery(limit=10))) == 1
    path.unlink()
    assert await adapter.search(MemoryQuery(limit=10)) == []


# --------------------------------------------------------------------------
# write — read-only
# --------------------------------------------------------------------------


async def test_write_raises(adapter: MarkdownFolderAdapter, agent_id):
    entry = MemoryEntry(agent_id=agent_id, type=MemoryType.SEMANTIC, content="x")
    with pytest.raises(MemoryWriteError):
        await adapter.write(entry)


def test_capabilities_declare_read_only():
    caps = MarkdownFolderAdapter.CAPABILITIES
    assert caps.is_writable is False
    assert caps.supports_vector is False
    assert caps.supports_full_text is False
    assert caps.supports_tags is True
    assert caps.supports_time_range is True
    assert caps.is_persistent is True


# --------------------------------------------------------------------------
# health_check
# --------------------------------------------------------------------------


async def test_health_ok(adapter: MarkdownFolderAdapter):
    health = await adapter.health_check()
    assert health.healthy is True


async def test_health_missing_dir(tmp_path: Path, agent_id):
    adapter = MarkdownFolderAdapter(tmp_path / "does-not-exist", agent_id)
    health = await adapter.health_check()
    assert health.healthy is False and health.message


async def test_health_not_a_directory(tmp_path: Path, agent_id):
    file_path = _write(tmp_path, "a-file.md", "body")
    adapter = MarkdownFolderAdapter(file_path, agent_id)
    health = await adapter.health_check()
    assert health.healthy is False and health.message


async def test_health_unreadable_dir(tmp_path: Path, agent_id):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    try:
        health = await MarkdownFolderAdapter(locked, agent_id).health_check()
    finally:
        os.chmod(locked, 0o755)  # restore so tmp cleanup can remove it
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission checks")
    assert health.healthy is False and health.message


# --------------------------------------------------------------------------
# Skips
# --------------------------------------------------------------------------


async def test_skips_dotdirs_and_dotfiles(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "keep.md", "keep")
    _write(tmp_path, ".obsidian/config.md", "ignore")
    _write(tmp_path, ".trash/old.md", "ignore")
    _write(tmp_path, ".hidden.md", "ignore")
    results = await adapter.search(MemoryQuery(limit=10))
    assert [e.content for e in results] == ["keep"]


async def test_custom_ignore_globs(tmp_path: Path, agent_id):
    _write(tmp_path, "keep.md", "keep")
    _write(tmp_path, "templates/tpl.md", "ignore")
    adapter = MarkdownFolderAdapter(tmp_path, agent_id, ignore_globs=["templates/**"])
    results = await adapter.search(MemoryQuery(limit=10))
    assert [e.content for e in results] == ["keep"]


async def test_skips_oversized_files(tmp_path: Path, agent_id):
    _write(tmp_path, "small.md", "small")
    _write(tmp_path, "big.md", "x" * 5000)
    adapter = MarkdownFolderAdapter(tmp_path, agent_id, max_file_bytes=1000)
    results = await adapter.search(MemoryQuery(limit=10))
    assert [e.content for e in results] == ["small"]


async def test_skips_non_markdown(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "note.md", "markdown")
    _write(tmp_path, "readme.txt", "text")
    _write(tmp_path, "data.json", "{}")
    results = await adapter.search(MemoryQuery(limit=10))
    assert [e.content for e in results] == ["markdown"]


async def test_reads_markdown_extension(adapter: MarkdownFolderAdapter, tmp_path: Path):
    _write(tmp_path, "note.markdown", "long extension")
    results = await adapter.search(MemoryQuery(limit=10))
    assert [e.content for e in results] == ["long extension"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform lacks symlinks")
async def test_symlinks_not_followed(adapter: MarkdownFolderAdapter, tmp_path: Path):
    real = _write(tmp_path, "real.md", "real")
    try:
        (tmp_path / "link.md").symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")
    results = await adapter.search(MemoryQuery(limit=10))
    assert [e.content for e in results] == ["real"]  # the symlink is skipped


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------


def test_is_a_memory_adapter(adapter: MarkdownFolderAdapter):
    assert isinstance(adapter, MemoryAdapter)
