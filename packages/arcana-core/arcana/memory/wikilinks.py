"""WikilinkEdgeExtractor — turn a folder's ``[[wikilinks]]`` into graph edges.

Parses Obsidian-style links out of a Markdown folder and writes them to
``memory_edges`` as directed ``references`` edges between the notes' stable ids.
Deterministic end to end — regex over text the reader already loaded, resolution by
filename/path, no model in the loop — so it carries none of the hallucinated-edge
risk that gates inferred edges. It is the first concrete population of the memory
graph, and it needs nothing Obsidian-specific: a plain folder, the same one the
reader serves.

Resolution is confined to the folder's own notes (Obsidian's shortest-path rule):
a bare ``[[Note]]`` resolves by basename, a ``[[folder/Note]]`` by relative path.
Ambiguous names and dangling targets are skipped and counted, never guessed. Each
re-index rewrites the whole ``wikilink`` edge set, so added, removed, or re-pointed
links converge without per-edge diffing.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from arcana.memory.adapters.markdown import MarkdownFolderAdapter, ScannedNote
from arcana.memory.edges import EdgeStore
from arcana.types import MemoryEdge

logger = logging.getLogger("arcana.memory.wikilinks")

#: The relation and producer tag every extracted wikilink edge carries. The tag
#: lets a re-index replace exactly this producer's edges (see ``EdgeStore``).
WIKILINK_RELATION = "references"
WIKILINK_SOURCE = "wikilink"

#: ``[[Target]]`` and its embed form ``![[Target]]``. The inner capture excludes
#: brackets so nested/edge cases don't span link boundaries; alias/anchor are
#: trimmed later, not here.
_WIKILINK_RE = re.compile(r"!?\[\[([^\[\]]+?)\]\]")
#: Fenced blocks and inline code — stripped before scanning so a ``[[x]]`` shown as
#: code is not mistaken for a link.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")

_MD_SUFFIXES = (".md", ".markdown")


@dataclass(frozen=True)
class EdgeIndexReport:
    """Outcome of a re-index pass. All counts are per-occurrence except the last."""

    notes_scanned: int = 0
    links_found: int = 0  # total link occurrences seen (pre-dedup)
    resolved: int = 0  # occurrences that resolved to a note
    dangling: int = 0  # occurrences whose target has no matching note
    ambiguous: int = 0  # occurrences whose bare name matched multiple notes
    edges_written: int = 0  # distinct edges written (self-links and dupes dropped)


class WikilinkEdgeExtractor:
    """Extract ``[[wikilink]]`` edges from a folder into an ``EdgeStore``."""

    def __init__(self, reader: MarkdownFolderAdapter, edges: EdgeStore) -> None:
        self._reader = reader
        self._edges = edges

    async def reindex(self) -> EdgeIndexReport:
        """Scan the folder, resolve every wikilink, and rewrite the edge set.

        Rewrites the whole ``wikilink`` source in one shot, so the graph reflects
        exactly the links present on disk right now. Returns a tally of what was
        found and written.
        """
        notes = await self._reader.scan()
        resolver = _Resolver(notes)
        stamped = datetime.now(UTC)

        edges: list[MemoryEdge] = []
        seen: set[tuple[str, str]] = set()  # (src, dst) pair keys, for dedup
        links_found = resolved = dangling = ambiguous = 0

        for note in notes:
            for raw in _wikilink_targets(note.entry.content):
                target = _clean_target(raw)
                if not target:
                    continue
                links_found += 1
                match, status = resolver.resolve(target)
                if status == _Status.AMBIGUOUS:
                    ambiguous += 1
                    logger.debug("ambiguous wikilink %r in %s; skipped", target, note.rel_path)
                    continue
                if match is None:
                    dangling += 1
                    logger.debug("dangling wikilink %r in %s; skipped", target, note.rel_path)
                    continue
                resolved += 1
                if match.entry.id == note.entry.id:
                    continue  # self-link
                key = (str(note.entry.id), str(match.entry.id))
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    MemoryEdge(
                        src_id=note.entry.id,
                        dst_id=match.entry.id,
                        relation=WIKILINK_RELATION,
                        confidence=1.0,
                        source=WIKILINK_SOURCE,
                        created_at=stamped,
                    )
                )

        written = await self._edges.replace_source(WIKILINK_SOURCE, edges)
        return EdgeIndexReport(
            notes_scanned=len(notes),
            links_found=links_found,
            resolved=resolved,
            dangling=dangling,
            ambiguous=ambiguous,
            edges_written=written,
        )


# ---------------------------------------------------------------------------
# Parsing + resolution (module-level, pure)
# ---------------------------------------------------------------------------


class _Status:
    """Resolution outcomes (a small closed set, no enum ceremony needed)."""

    RESOLVED = "resolved"
    DANGLING = "dangling"
    AMBIGUOUS = "ambiguous"


def _wikilink_targets(body: str) -> list[str]:
    """Raw link targets (inner text of ``[[...]]``/``![[...]]``), code stripped."""
    stripped = _INLINE_CODE_RE.sub(" ", _FENCE_RE.sub(" ", body))
    return [match.group(1) for match in _WIKILINK_RE.finditer(stripped)]


def _clean_target(raw: str) -> str:
    """Reduce a raw link body to its note target: drop alias, anchor, extension."""
    target = raw.split("|", 1)[0]  # [[Target|Alias]] → Target
    target = target.split("#", 1)[0]  # [[Target#Heading]] / #^block → Target
    target = target.strip()
    lowered = target.lower()
    for suffix in _MD_SUFFIXES:
        if lowered.endswith(suffix):
            return target[: -len(suffix)].strip()
    return target


class _Resolver:
    """Resolves a link target to a note within one folder (Obsidian-style)."""

    def __init__(self, notes: list[ScannedNote]) -> None:
        self._by_stem: dict[str, list[ScannedNote]] = {}
        self._by_path: dict[str, ScannedNote] = {}
        for note in notes:
            path = PurePosixPath(note.rel_path)
            self._by_stem.setdefault(path.stem.lower(), []).append(note)
            # Key by the extension-less relative path for path-qualified targets.
            without_ext = note.rel_path[: -len(path.suffix)] if path.suffix else note.rel_path
            self._by_path[without_ext.lower()] = note

    def resolve(self, target: str) -> tuple[ScannedNote | None, str]:
        key = target.lower().lstrip("/")
        if "/" in key:
            note = self._by_path.get(key)
            return (note, _Status.RESOLVED) if note is not None else (None, _Status.DANGLING)
        matches = self._by_stem.get(key, [])
        if len(matches) == 1:
            return matches[0], _Status.RESOLVED
        if len(matches) > 1:
            return None, _Status.AMBIGUOUS
        return None, _Status.DANGLING
