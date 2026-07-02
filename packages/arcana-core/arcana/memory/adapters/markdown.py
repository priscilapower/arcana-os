"""MarkdownFolderAdapter — a read-oriented ``MemoryAdapter`` over a folder of notes.

Exposes a directory of Markdown files as retrievable memory: one ``.md``/``.markdown``
file becomes one :class:`MemoryEntry`. It satisfies the same ``MemoryAdapter`` protocol
as every other backend, so it can be registered as a tier in a ``MemoryFederation`` or
attached directly to an agent's ``memory:`` slot with nothing but a folder path — no
plugin, no server, no sync job.

The folder has no full-text or vector index, so every retrieval mode collapses to an
in-process keyword scan: parse → filter → rank. A live read stays fresh via a
process-local index cache keyed by path → (mtime, size, entry); each ``search`` restats
the tree (cheap) and re-parses only changed or new files. Filesystem I/O runs through
``asyncio.to_thread`` so the event loop is never blocked, mirroring how ``SQLiteAdapter``
keeps disk work off the loop. The source is treated as read-only: reads never mutate
files, and ``write`` raises :class:`MemoryWriteError`.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from arcana.memory.errors import MemoryWriteError
from arcana.types import (
    AdapterCapabilities,
    AdapterHealth,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetrievalMode,
)

logger = logging.getLogger("arcana.memory.markdown")

#: Fixed namespace for deriving a stable, idempotent entry id from a file's
#: root-relative POSIX path. Re-reading the same file yields the same id, so a
#: future ingest can upsert without creating duplicates. Derived from a readable
#: seed rather than a magic literal — the value only needs to be constant.
_MD_NAMESPACE = uuid5(NAMESPACE_URL, "arcana.memory.markdown")

#: Recognised Markdown extensions (lower-cased comparison).
_MD_SUFFIXES = (".md", ".markdown")

#: Default directories excluded from a scan. Dot-dirs are skipped unconditionally;
#: these globs are the visible, documented defaults a caller can override.
_DEFAULT_IGNORE_GLOBS: list[str] = [".obsidian/**", ".trash/**"]

#: Obsidian ``#hashtag`` at a word boundary. Requires a non-digit leading char so a
#: Markdown ``# Heading`` (space after ``#``) and bare ``#123`` are not tagged.
_HASHTAG_RE = re.compile(r"(?:(?<=\s)|^)#([A-Za-z_][A-Za-z0-9_/-]*)")

#: Truthy scalar spellings for boolean frontmatter values (e.g. ``pinned:``).
_TRUE_TOKENS = frozenset({"true", "yes", "on", "1"})


@dataclass(frozen=True)
class _Doc:
    """A parsed note: its entry plus the path data used for filtering and ranking."""

    entry: MemoryEntry
    rel_path: str  # root-relative POSIX path — also the cache key
    haystack: str  # lower-cased content + tags + path, precomputed for scans


@dataclass(frozen=True)
class _CacheItem:
    """One index-cache slot: the fingerprint that decides staleness plus the doc."""

    mtime: float
    size: int
    doc: _Doc


@dataclass(frozen=True)
class ScannedNote:
    """A note as seen by a consumer: its ``entry`` plus the on-disk ``rel_path``.

    The public projection of the folder's parsed view. Exposes the root-relative
    POSIX path alongside the entry so a consumer (e.g. wikilink resolution) can map
    link targets to notes without re-walking the tree.
    """

    entry: MemoryEntry
    rel_path: str


class MarkdownFolderAdapter:
    """Read-only ``MemoryAdapter`` backed by a folder of Markdown notes.

    One instance owns exactly one folder ``root`` and is scoped to one ``agent_id``;
    composing multiple folders is the federation layer's job. Implements ``search``,
    ``write`` (raises — the source is read-only), and ``health_check``.
    """

    CAPABILITIES = AdapterCapabilities(
        supports_vector=False,
        supports_full_text=False,  # substring/token scan, not a real FTS index
        supports_tags=True,
        supports_time_range=True,
        is_writable=False,
        is_persistent=True,
    )

    def __init__(
        self,
        root: Path,
        agent_id: UUID,
        *,
        scope: MemoryScope = MemoryScope.PRIVATE,
        pool_name: str | None = None,
        default_type: MemoryType = MemoryType.SEMANTIC,
        ignore_globs: list[str] = _DEFAULT_IGNORE_GLOBS,
        max_file_bytes: int = 1_048_576,
    ) -> None:
        self._root = Path(root)
        self._agent_id = agent_id
        self._scope = scope
        self._pool_name = pool_name
        self._default_type = default_type
        # Copy so a caller (or the shared module default) can't mutate our config.
        self._ignore_globs = list(ignore_globs)
        self._max_file_bytes = max_file_bytes
        #: path -> cached fingerprint + doc. Rebuilt wholesale each scan (last write
        #: wins) so concurrent scans never observe a half-updated index.
        self._index: dict[str, _CacheItem] = {}
        #: Categories already warned about, so a degraded notice logs at most once.
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    # MemoryAdapter protocol
    # ------------------------------------------------------------------

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Scan the folder and return entries matching ``query``, keyword-ranked.

        The folder has no semantic or full-text index, so ``semantic``/``hybrid``
        requests are served by the same keyword path with a one-line degraded
        notice — the mode is never an error. Ranking is pinned → lexical relevance
        → importance → recency, ties broken by path for determinism, truncated to
        ``query.limit``.
        """
        if query.retrieval_mode is not RetrievalMode.keyword:
            self._warn_once(
                f"mode:{query.retrieval_mode.value}",
                f"MarkdownFolderAdapter has no {query.retrieval_mode.value} index; "
                "serving keyword results for this query.",
            )

        # A query pinned to a different scope/pool/agent than this adapter owns can
        # never match — short-circuit before touching the disk.
        if query.scope is not None and query.scope is not self._scope:
            return []
        if query.pool_name is not None and query.pool_name != self._pool_name:
            return []
        if query.agent_id is not None and query.agent_id != self._agent_id:
            return []

        docs = await asyncio.to_thread(self._rescan)
        return self._filter_and_rank(docs, query)

    async def write(self, entry: MemoryEntry) -> None:
        """Reject writes — the folder is an external source of truth, not a sink.

        Silently dropping a write would hide data loss, and the federation's writer
        propagates tier failures deliberately, so a read-only tier must say so.
        """
        raise MemoryWriteError("MarkdownFolderAdapter is read-only; configure a writable tier for persistence")

    async def health_check(self) -> AdapterHealth:
        """Report whether the root is a readable directory. Never raises.

        Kept cheap — a single ``stat`` + ``os.access`` on the root, no tree walk —
        so the resilience layer can use it as a half-open recovery probe.
        """
        return await asyncio.to_thread(self._health)

    # ------------------------------------------------------------------
    # Graph / inspection surface
    # ------------------------------------------------------------------

    async def scan(self) -> list[ScannedNote]:
        """Return every note as a public ``(entry, rel_path)`` pair.

        Exposes the folder's parsed view — the same cached scan ``search`` uses —
        for consumers that need the on-disk path alongside the entry, such as
        resolving wikilink targets to note ids.
        """
        docs = await asyncio.to_thread(self._rescan)
        return [ScannedNote(entry=doc.entry, rel_path=doc.rel_path) for doc in docs]

    async def get(self, entry_id: UUID) -> MemoryEntry | None:
        """Return the note with stable id ``entry_id``, or ``None`` if absent.

        Lets a graph-expansion read rehydrate a folder-resident neighbour by id.
        The id is a one-way ``uuid5`` of the path, so this resolves by matching over
        the (cached) tree rather than reversing the hash.
        """
        docs = await asyncio.to_thread(self._rescan)
        for doc in docs:
            if doc.entry.id == entry_id:
                return doc.entry
        return None

    # ------------------------------------------------------------------
    # Scanning + cache
    # ------------------------------------------------------------------

    def _rescan(self) -> list[_Doc]:
        """Restat the tree, re-parsing only changed/new files; drop deleted ones.

        Runs in a worker thread. Builds a fresh index and swaps it in atomically at
        the end so a concurrent scan never sees a partially-updated cache.
        """
        new_index: dict[str, _CacheItem] = {}
        docs: list[_Doc] = []

        for rel_path, path, stat in self._discover():
            cached = self._index.get(rel_path)
            if cached is not None and cached.mtime == stat.st_mtime and cached.size == stat.st_size:
                doc = cached.doc
            else:
                parsed = self._parse_file(path, rel_path, stat)
                if parsed is None:  # unreadable/undecodable — skip, don't fail the scan
                    continue
                doc = parsed
            new_index[rel_path] = _CacheItem(mtime=stat.st_mtime, size=stat.st_size, doc=doc)
            docs.append(doc)

        self._index = new_index
        return docs

    def _discover(self) -> list[tuple[str, Path, os.stat_result]]:
        """Yield ``(rel_posix, path, stat)`` for every eligible note under the root.

        Skips dotfiles/dot-dirs, symlinks (no cycles, no escaping the root),
        ignore-glob matches, non-Markdown files, and anything over ``max_file_bytes``.
        """
        root = self._root
        found: list[tuple[str, Path, os.stat_result]] = []

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Prune hidden directories in place so os.walk never descends into them.
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                if not name.lower().endswith(_MD_SUFFIXES):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                rel_path = path.relative_to(root).as_posix()
                if any(fnmatch(rel_path, pattern) for pattern in self._ignore_globs):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size > self._max_file_bytes:
                    self._warn_once(
                        f"oversize:{rel_path}",
                        f"skipping {rel_path}: {stat.st_size} bytes exceeds cap of {self._max_file_bytes}",
                    )
                    continue
                found.append((rel_path, path, stat))
        return found

    def _parse_file(self, path: Path, rel_path: str, stat: os.stat_result) -> _Doc | None:
        """Read one note into a ``_Doc``. Returns ``None`` if the file can't be read."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("skipping unreadable note %s: %s", rel_path, exc)
            return None

        meta: dict[str, str | list[str]]
        try:
            meta, body = _split_frontmatter(text)
        except _FrontmatterError as exc:
            # One bad note must not fail the scan: treat the whole file as body.
            logger.debug("malformed frontmatter in %s (%s); treating file as body", rel_path, exc)
            meta, body = {}, text

        entry = MemoryEntry(
            id=uuid5(_MD_NAMESPACE, rel_path),
            agent_id=self._agent_id,
            type=_coerce_type(meta.get("type"), self._default_type),
            content=body,
            scope=self._scope,
            pool_name=self._pool_name,
            importance=_coerce_importance(meta.get("importance")),
            pinned=_coerce_bool(meta.get("pinned")),
            tags=_collect_tags(meta.get("tags"), body),
            embedding=None,
            created_at=datetime.fromtimestamp(stat.st_ctime, tz=UTC),
            last_accessed_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )
        haystack = "\n".join((entry.content, " ".join(entry.tags), rel_path)).lower()
        return _Doc(entry=entry, rel_path=rel_path, haystack=haystack)

    # ------------------------------------------------------------------
    # Filter + rank
    # ------------------------------------------------------------------

    def _filter_and_rank(self, docs: list[_Doc], query: MemoryQuery) -> list[MemoryEntry]:
        terms = _relevance_terms(query)
        matched = [doc for doc in docs if self._matches(doc, query)]
        matched.sort(key=lambda doc: self._rank_key(doc, terms))
        return [doc.entry for doc in matched[: query.limit]]

    def _matches(self, doc: _Doc, query: MemoryQuery) -> bool:
        """Apply the ``MemoryQuery`` filters this adapter can honour.

        ``min_confidence`` and ``include_archived`` are no-ops (folder entries are
        confidence 1.0 and never archived). ``pinned`` has no query field, so it
        only influences ranking, not membership.
        """
        entry = doc.entry
        if query.type is not None and entry.type is not query.type:
            return False
        if any(tag not in entry.tags for tag in query.tags):
            return False
        if entry.importance < query.min_importance:
            return False
        if query.time_from is not None and entry.last_accessed_at < query.time_from:
            return False
        if query.time_to is not None and entry.last_accessed_at > query.time_to:
            return False

        # Every explicit keyword must appear (AND); the free-text query needs any of
        # its tokens (OR) or a full-string hit — mirroring keyword retrieval intent.
        for keyword in query.keywords:
            if keyword.lower() not in doc.haystack:
                return False
        if query.text:
            text = query.text.lower()
            text_terms = text.split()
            if text_terms and not any(term in doc.haystack for term in text_terms) and text not in doc.haystack:
                return False
        return True

    def _rank_key(self, doc: _Doc, terms: list[str]) -> tuple[bool, int, float, float, str]:
        """Sort key (ascending): pinned first, then relevance, importance, recency, path.

        Negations flip the descending fields; ``rel_path`` breaks ties for a stable,
        deterministic order.
        """
        entry = doc.entry
        relevance = sum(doc.haystack.count(term) for term in terms)
        return (
            not entry.pinned,
            -relevance,
            -entry.importance,
            -entry.last_accessed_at.timestamp(),
            doc.rel_path,
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def _health(self) -> AdapterHealth:
        adapter_id = str(self._root)
        try:
            if not self._root.exists():
                return AdapterHealth(adapter_id=adapter_id, healthy=False, message="root does not exist")
            if not self._root.is_dir():
                return AdapterHealth(adapter_id=adapter_id, healthy=False, message="root is not a directory")
            if not os.access(self._root, os.R_OK):
                return AdapterHealth(adapter_id=adapter_id, healthy=False, message="root is not readable")
        except OSError as exc:  # e.g. a permission error on stat
            return AdapterHealth(adapter_id=adapter_id, healthy=False, message=str(exc))
        return AdapterHealth(adapter_id=adapter_id, healthy=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _warn_once(self, key: str, message: str) -> None:
        """Emit a warning at most once per category for this adapter instance."""
        if key not in self._warned:
            logger.warning(message)
            self._warned.add(key)


# ---------------------------------------------------------------------------
# Frontmatter + field coercion (module-level, pure)
# ---------------------------------------------------------------------------


class _FrontmatterError(Exception):
    """Raised internally when a ``---`` block is present but not parseable."""


def _split_frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """Split a document into ``(frontmatter, body)``.

    Recognises a leading ``---`` … ``---`` YAML block. When there is no well-formed
    block, returns ``({}, text)``. A block that opens but never closes, or whose
    contents don't parse, raises :class:`_FrontmatterError`, which the caller treats
    as "no frontmatter — the whole file is body".
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return {}, text

    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            meta = _parse_block([ln.rstrip("\r\n") for ln in lines[1:i]])
            body = "".join(lines[i + 1 :]).lstrip("\r\n")
            return meta, body

    raise _FrontmatterError("unterminated frontmatter block")


def _parse_block(block: list[str]) -> dict[str, str | list[str]]:
    """Parse the handful of scalar/list keys the adapter cares about from a YAML block.

    Supports ``key: scalar``, inline lists ``key: [a, b]``, and block lists
    (``key:`` followed by indented ``- item`` lines). Unknown keys are kept but
    ignored downstream. Anything that isn't a list item and lacks a ``:`` is a parse
    failure, surfaced as :class:`_FrontmatterError`.
    """
    meta: dict[str, str | list[str]] = {}
    pending: list[str] | None = None

    for line in block:
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped == "-" or stripped.startswith("- "):
            if pending is None:
                raise _FrontmatterError(f"list item without a key: {line!r}")
            item = _unquote(stripped[1:].strip())
            if item:
                pending.append(item)
            continue
        key, sep, raw = line.partition(":")
        key = key.strip()
        if not sep or not key:
            raise _FrontmatterError(f"not a key-value line: {line!r}")
        value = raw.strip()
        if value == "":
            # Bare key — a block list may follow on the next indented lines.
            pending = []
            meta[key] = pending
        else:
            pending = None
            meta[key] = _parse_inline(value)
    return meta


def _parse_inline(value: str) -> str | list[str]:
    """Parse a scalar or an inline ``[a, b, c]`` list from a frontmatter value."""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
    return _unquote(value)


def _unquote(value: str) -> str:
    """Strip a single pair of matching surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _coerce_type(raw: object, default: MemoryType) -> MemoryType:
    """Map a frontmatter ``type:`` to a ``MemoryType``; fall back to ``default``."""
    if isinstance(raw, str):
        try:
            return MemoryType(raw.strip().lower())
        except ValueError:
            return default
    return default


def _coerce_importance(raw: object) -> float:
    """Parse ``importance:`` clamped to ``[0.0, 1.0]``; default ``0.5``."""
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return _clamp01(float(raw))
    if isinstance(raw, str):
        try:
            return _clamp01(float(raw.strip()))
        except ValueError:
            return 0.5
    return 0.5


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _coerce_bool(raw: object) -> bool:
    """Parse a frontmatter boolean (e.g. ``pinned:``); default ``False``."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int | float):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in _TRUE_TOKENS
    return False


def _collect_tags(raw: str | list[str] | None, body: str) -> list[str]:
    """Union of frontmatter ``tags:`` (list or comma string) and body ``#hashtags``.

    Order is preserved and duplicates removed; ``[[wikilinks]]`` are not resolved.
    """
    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)

    if isinstance(raw, list):
        for item in raw:
            add(item)
    elif isinstance(raw, str):
        for part in raw.split(","):
            add(part)

    for match in _HASHTAG_RE.finditer(body):
        add(match.group(1))

    return tags


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _relevance_terms(query: MemoryQuery) -> list[str]:
    """Lower-cased scoring terms drawn from the query's free text and keywords."""
    terms: list[str] = []
    if query.text:
        terms.extend(query.text.lower().split())
    terms.extend(keyword.lower() for keyword in query.keywords)
    return terms
