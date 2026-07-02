"""Unit tests for WikilinkEdgeExtractor and its pure helpers. No LLM calls."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from arcana.memory import (
    EdgeStore,
    MarkdownFolderAdapter,
    SQLiteAdapter,
    WikilinkEdgeExtractor,
)
from arcana.memory.wikilinks import _clean_target, _Resolver, _wikilink_targets
from arcana.types import MemoryScope


def _write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def agent_id() -> UUID:
    return uuid4()


async def _extractor(root: Path, agent: UUID) -> tuple[WikilinkEdgeExtractor, EdgeStore, MarkdownFolderAdapter]:
    reader = MarkdownFolderAdapter(root, agent)
    edges = EdgeStore(SQLiteAdapter(root / "memory.db"))
    await edges.connect()
    return WikilinkEdgeExtractor(reader, edges), edges, reader


async def _edge_pairs(edges: EdgeStore, reader: MarkdownFolderAdapter) -> set[tuple[str, str]]:
    names = {n.entry.id: n.rel_path for n in await reader.scan()}
    return {(names[e.src_id], names[e.dst_id]) for e in await edges.all()}


# --------------------------------------------------------------------------
# Pure parsing helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("see [[Target]]", ["Target"]),
        ("alias [[Target|Display]]", ["Target|Display"]),
        ("embed ![[Target]]", ["Target"]),
        ("two [[A]] and [[B]]", ["A", "B"]),
        ("none here", []),
        ("code `[[NotALink]]` stays out", []),
        ("fenced\n```\n[[NotALink]]\n```\ndone", []),
    ],
)
def test_wikilink_targets(body: str, expected: list[str]):
    assert _wikilink_targets(body) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Target", "Target"),
        ("Target|Alias", "Target"),
        ("Target#Heading", "Target"),
        ("Target#^blockid", "Target"),
        ("folder/Note", "folder/Note"),
        ("Note.md", "Note"),
        ("Note.markdown", "Note"),
        ("#only-anchor", ""),
    ],
)
def test_clean_target(raw: str, expected: str):
    assert _clean_target(raw) == expected


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


async def test_resolver_by_stem_and_path(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "Alpha.md", "x")
    _write(tmp_path, "sub/Gamma.md", "x")
    reader = MarkdownFolderAdapter(tmp_path, agent_id)
    resolver = _Resolver(await reader.scan())

    hit, status = resolver.resolve("Alpha")
    assert status == "resolved" and hit is not None and hit.rel_path == "Alpha.md"

    hit, status = resolver.resolve("sub/Gamma")  # path-qualified
    assert status == "resolved" and hit is not None and hit.rel_path == "sub/Gamma.md"

    hit, status = resolver.resolve("alpha")  # case-insensitive
    assert status == "resolved"


async def test_resolver_dangling_and_ambiguous(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "one/Note.md", "x")
    _write(tmp_path, "two/Note.md", "x")  # same stem in two folders
    _write(tmp_path, "Unique.md", "x")
    reader = MarkdownFolderAdapter(tmp_path, agent_id)
    resolver = _Resolver(await reader.scan())

    assert resolver.resolve("Missing") == (None, "dangling")

    hit, status = resolver.resolve("Note")  # ambiguous bare name
    assert hit is None and status == "ambiguous"

    hit, status = resolver.resolve("two/Note")  # path disambiguates
    assert status == "resolved" and hit is not None and hit.rel_path == "two/Note.md"


# --------------------------------------------------------------------------
# reindex — end to end
# --------------------------------------------------------------------------


async def test_reindex_builds_resolved_edges(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "alpha.md", "Alpha → [[Beta]] and [[sub/Gamma]].")
    _write(tmp_path, "beta.md", "Beta → [[Alpha]].")
    _write(tmp_path, "sub/Gamma.md", "Gamma embeds ![[Alpha]].")
    extractor, edges, reader = await _extractor(tmp_path, agent_id)

    report = await extractor.reindex()
    assert report.notes_scanned == 3
    assert report.edges_written == 4
    assert await _edge_pairs(edges, reader) == {
        ("alpha.md", "beta.md"),
        ("alpha.md", "sub/Gamma.md"),
        ("beta.md", "alpha.md"),
        ("sub/Gamma.md", "alpha.md"),
    }
    await edges.aclose()


async def test_reindex_counts_dangling_and_ambiguous(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "a.md", "[[Missing]] and [[Note]]")
    _write(tmp_path, "x/Note.md", "x")
    _write(tmp_path, "y/Note.md", "x")  # makes bare [[Note]] ambiguous
    extractor, edges, _ = await _extractor(tmp_path, agent_id)

    report = await extractor.reindex()
    assert report.links_found == 2
    assert report.dangling == 1  # Missing
    assert report.ambiguous == 1  # Note
    assert report.edges_written == 0
    await edges.aclose()


async def test_reindex_drops_self_links(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "solo.md", "I link to [[Solo]] myself.")
    extractor, edges, _ = await _extractor(tmp_path, agent_id)
    report = await extractor.reindex()
    assert report.resolved == 1 and report.edges_written == 0
    await edges.aclose()


async def test_reindex_dedups_repeated_links(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "a.md", "[[B]] again [[B]] and once more [[B]]")
    _write(tmp_path, "b.md", "b")
    extractor, edges, _ = await _extractor(tmp_path, agent_id)
    report = await extractor.reindex()
    assert report.links_found == 3 and report.edges_written == 1
    await edges.aclose()


async def test_reindex_is_idempotent(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "a.md", "[[B]]")
    _write(tmp_path, "b.md", "[[A]]")
    extractor, edges, _ = await _extractor(tmp_path, agent_id)
    await extractor.reindex()
    await extractor.reindex()
    assert await edges.count() == 2  # no duplication across passes
    await edges.aclose()


async def test_reindex_reflects_added_removed_and_renamed(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "a.md", "[[B]]")
    _write(tmp_path, "b.md", "b")
    extractor, edges, reader = await _extractor(tmp_path, agent_id)
    await extractor.reindex()
    assert await edges.count() == 1

    # Remove the link → edge disappears on re-index.
    _write(tmp_path, "a.md", "no links now")
    await extractor.reindex()
    assert await edges.count() == 0

    # Add a link to a new target → edge appears.
    _write(tmp_path, "a.md", "[[C]]")
    _write(tmp_path, "c.md", "c")
    await extractor.reindex()
    assert await _edge_pairs(edges, reader) == {("a.md", "c.md")}
    await edges.aclose()


async def test_reindex_ignores_code_and_tags(tmp_path: Path, agent_id: UUID):
    _write(tmp_path, "a.md", "real [[B]]\n```\n[[B]]\n```\ninline `[[B]]` and #btag")
    _write(tmp_path, "b.md", "b")
    extractor, edges, _ = await _extractor(tmp_path, agent_id)
    report = await extractor.reindex()
    assert report.links_found == 1 and report.edges_written == 1  # only the real one
    await edges.aclose()


async def test_edges_survive_scope_and_pool_config(tmp_path: Path, agent_id: UUID):
    # A shared-scoped reader still produces edges keyed by the same stable ids.
    _write(tmp_path, "a.md", "[[B]]")
    _write(tmp_path, "b.md", "b")
    reader = MarkdownFolderAdapter(tmp_path, agent_id, scope=MemoryScope.SHARED, pool_name="vault")
    edges = EdgeStore(SQLiteAdapter(tmp_path / "memory.db"))
    await edges.connect()
    report = await WikilinkEdgeExtractor(reader, edges).reindex()
    assert report.edges_written == 1
    await edges.aclose()
