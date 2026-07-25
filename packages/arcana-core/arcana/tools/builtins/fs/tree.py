"""The guarded tree walk the recursive filesystem builtins are built on.

:mod:`~arcana.tools.builtins.fs.pathguard` confines *one* path; this module
confines a whole subtree. ``copy``, ``delete_dir``, and the cross-filesystem
``move`` fallback each descend an arbitrary tree the model named, which is where
the single-file guard stops being enough:

* **a symlink inside the tree is an escape.** ``shutil.copytree`` and
  ``shutil.rmtree`` follow directory symlinks by default, which turns a copy into
  an exfiltration primitive and a recursive delete into a delete-anything
  primitive. :class:`GuardedTree` never follows one: a link is yielded *as* a
  link and never descended, and every non-link node is re-tested for containment
  as it is reached, so a symlink swapped in mid-walk is caught rather than
  traversed;
* **recursion amplifies.** One call can touch an unbounded number of bytes,
  files, and directory levels, none of which a per-file byte cap bounds. The
  caps here are checked *during* the walk, so an over-budget tree is refused
  before it has been fully materialized rather than after.

Every refusal is a :class:`~arcana.tools.builtins.fs.pathguard.PathBlocked`, so
the handlers turn a cap or an escape into the same failed ``ToolResult`` as any
other blocked path.
"""

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from arcana.tools.builtins.fs.pathguard import PathBlocked, PathGuard, canonical_path, is_within
from arcana.types.tool import FsEntryKind


@dataclass(frozen=True, slots=True)
class TreeCaps:
    """The aggregate bounds one tree operation may not exceed."""

    max_tree_bytes: int
    max_file_count: int
    max_depth: int


#: The caps for a walk that must not be refusable — cleaning up a staging tree
#: this code built, or the superseded destination it replaced. Those walks are
#: not a model's request and must not inherit the budget a model's request just
#: exhausted: a rollback that stops at a cap because the *failure* was a cap is
#: no rollback at all. Containment and the no-follow rule still apply.
UNBOUNDED = TreeCaps(max_tree_bytes=sys.maxsize, max_file_count=sys.maxsize, max_depth=sys.maxsize)


@dataclass(frozen=True, slots=True)
class WalkEntry:
    """One node of a guarded walk: where it is, what it is, how deep it sits."""

    path: Path
    kind: FsEntryKind
    size: int
    depth: int


@dataclass(slots=True)
class TreeStats:
    """What a completed tree operation touched — the shape, never the content."""

    files: int = 0
    dirs: int = 0
    bytes: int = 0
    skipped: int = 0

    @property
    def entries(self) -> int:
        return self.files + self.dirs + self.skipped


class GuardedTree:
    """Symlink-safe, capped recursion over a subtree inside one path jail.

    Bound to the same :class:`PathGuard` the single-path handlers use, so a tree
    operation is confined by exactly the containment rules a single read or
    write is — applied once per node instead of once per call.
    """

    def __init__(self, guard: PathGuard, caps: TreeCaps) -> None:
        self._guard = guard
        self._caps = caps

    def walk(self, root: Path, *, bounded: bool = True) -> Iterator[WalkEntry]:
        """Yield every node under ``root``, parents before children.

        Directories arrive before their contents, which is the order a copy
        needs to create them in; a delete reverses the materialized list to get
        children-before-parents instead. ``root`` itself is not yielded — the
        caller already holds it, and already had it resolved by the guard.

        Raises :class:`PathBlocked` on a node that escapes the jail, on a tree
        deeper than ``max_depth``, or once the entry count passes
        ``max_file_count``. The count is enforced here rather than by the caller
        so that *every* recursive operation inherits the bound. Pass
        ``bounded=False`` only for the cleanup walks :data:`UNBOUNDED` describes;
        containment and the no-follow rule are not optional either way.
        """
        caps = self._caps if bounded else UNBOUNDED
        yield from self._walk(root, depth=1, caps=caps, counter=_Counter(caps.max_file_count))

    def _walk(self, directory: Path, *, depth: int, caps: TreeCaps, counter: "_Counter") -> Iterator[WalkEntry]:
        if depth > caps.max_depth:
            raise PathBlocked(f"tree is deeper than the {caps.max_depth}-level limit")

        for entry in self._guard.scan(directory):
            # The guard scans a descriptor, which yields bare names, so the full
            # path is built from the directory it actually opened.
            path = directory / entry.name

            counter.take()
            if entry.kind is FsEntryKind.SYMLINK:
                # Handled as an opaque link: never descended, never dereferenced.
                yield WalkEntry(path=path, kind=entry.kind, size=entry.size, depth=depth)
                continue

            # Per-node containment. The parent cleared the jail and this entry is
            # not a link, so this should hold by construction — which is exactly
            # why checking is cheap insurance against the case where it does not
            # (a bind mount, or a link swapped in after the scan classified it).
            if not any(is_within(canonical_path(path), root) for root in self._guard.roots):
                raise PathBlocked("symlink escape mid-walk")

            yield WalkEntry(path=path, kind=entry.kind, size=entry.size, depth=depth)
            if entry.kind is FsEntryKind.DIR:
                yield from self._walk(path, depth=depth + 1, caps=caps, counter=counter)

    def copy_into(self, src: Path, dst: Path, *, max_file_bytes: int) -> TreeStats:
        """Copy the tree at ``src`` to ``dst``, which must not already exist.

        Each regular file is written with the guard's atomic temp-then-rename, so
        a destination file is never observed half-copied, and is bounded by
        ``max_file_bytes`` — a single oversize file fails the whole copy rather
        than landing truncated. A symlink is recreated only when its target
        stays inside the jail, and counts towards ``files``; one pointing out,
        and any FIFO, socket, or device node, is skipped and counted in
        ``skipped`` instead. The caller is responsible for removing a partial
        ``dst`` when this raises.
        """
        stats = TreeStats()
        dst.mkdir(parents=True)
        stats.dirs += 1

        for entry in self.walk(src):
            target = dst / entry.path.relative_to(src)
            if entry.kind is FsEntryKind.DIR:
                target.mkdir()
                stats.dirs += 1
            elif entry.kind is FsEntryKind.FILE:
                stats.bytes = self._copy_file(entry.path, target, stats.bytes, max_file_bytes)
                stats.files += 1
            elif entry.kind is FsEntryKind.SYMLINK and self._links_inside(entry.path):
                target.symlink_to(os.readlink(entry.path))
                stats.files += 1
            else:
                stats.skipped += 1
        return stats

    def copy_file(self, src: Path, dst: Path, *, max_file_bytes: int) -> TreeStats:
        """Copy one regular file — the non-recursive half of ``copy``.

        Shares the byte cap and the atomic write with :meth:`copy_into` so a
        single-file copy and a one-file tree behave identically.
        """
        stats = TreeStats(files=1)
        stats.bytes = self._copy_file(src, dst, 0, max_file_bytes)
        return stats

    def remove(self, root: Path, *, bounded: bool = True) -> TreeStats:
        """Recursively unlink the tree at ``root``, then ``root`` itself.

        Children before parents, and links unlinked in place rather than
        followed, so removing a tree that contains a symlink out of the jail
        removes the *link* and never its target. ``bounded=False`` is for the
        cleanup case :data:`UNBOUNDED` describes.
        """
        entries = list(self.walk(root, bounded=bounded))
        stats = TreeStats()
        for entry in reversed(entries):
            if entry.kind is FsEntryKind.DIR:
                entry.path.rmdir()
                stats.dirs += 1
            else:
                # missing_ok: the walk is materialized before the first removal,
                # so an entry that vanished in between is already the outcome we
                # wanted, not a reason to abandon a half-removed tree.
                entry.path.unlink(missing_ok=True)
                stats.files += 1
        root.rmdir()
        stats.dirs += 1
        return stats

    def measure(self, root: Path) -> TreeStats:
        """Walk ``root`` without touching it, to apply the caps up front.

        What makes a recursive delete fail *closed*: an over-budget or escaping
        tree is refused while the filesystem is still untouched.
        """
        stats = TreeStats()
        for entry in self.walk(root):
            if entry.kind is FsEntryKind.DIR:
                stats.dirs += 1
            else:
                stats.files += 1
        return stats

    def _copy_file(self, src: Path, dst: Path, running_bytes: int, max_file_bytes: int) -> int:
        """Copy one file under both the per-file and the aggregate byte caps."""
        data, truncated = self._guard.read_bytes(src, max_file_bytes)
        if truncated:
            raise PathBlocked(f"a file in the tree is larger than the {max_file_bytes}-byte per-file cap")

        total = running_bytes + len(data)
        if total > self._caps.max_tree_bytes:
            raise PathBlocked(f"tree is larger than the {self._caps.max_tree_bytes}-byte limit")

        self._guard.atomic_write(dst, data)
        return total

    def _links_inside(self, link: Path) -> bool:
        """True if ``link``'s target resolves inside the jail.

        A link is copied verbatim rather than dereferenced, so this decides only
        whether recreating it would hand the destination tree a door out of the
        jail — one that would not exist if we simply skipped it.
        """
        try:
            target = canonical_path(link)
        except PathBlocked:
            return False
        return any(is_within(target, root) for root in self._guard.roots)


class _Counter:
    """Entry budget for one walk, shared across its recursive calls."""

    __slots__ = ("_limit", "_seen")

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._seen = 0

    def take(self) -> None:
        self._seen += 1
        if self._seen > self._limit:
            raise PathBlocked(f"tree has more than the {self._limit}-entry limit")
