"""The safe-swap protocol behind ``move`` and ``copy``.

Both tools have to put something at a destination that may already hold data the
agent asked to replace, and both can fail half-way — on a cap, on an escaping
symlink mid-walk, on a full disk. Doing that naively loses data twice over: a
copy written straight into the destination leaves a half-built tree that looks
finished, and a destination deleted up front is gone even when the replacement
never arrives.

So nothing is put anywhere until there is something whole to put:

1. **stage** — the replacement is assembled in a dot-prefixed sibling of the
   destination. Every abort up to this point has exactly one thing to undo, and
   the destination has not been touched;
2. **displace** — an existing destination is *renamed aside*, not removed, so it
   can still be put back;
3. **install** — a single same-directory rename moves the staged replacement into
   place, so the window where the destination does not exist is one syscall wide;
4. **discard** — only now, with the replacement committed, is the displaced
   original dropped.

A failure at any step restores what was there. ``move`` short-circuits the whole
protocol when source and destination share a filesystem, because a rename is
already atomic and already leaves nothing behind.
"""

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from arcana.tools.builtins.fs.pathguard import PathBlocked, PathGuard
from arcana.tools.builtins.fs.tree import GuardedTree, TreeStats
from arcana.types._utils import now_utc

#: Marks the staging sibling a replacement is assembled in. Dot-prefixed so a
#: listing does not advertise a copy in flight, and suffixed so a leftover from a
#: killed process is identifiable as debris rather than as someone's data.
STAGING_PREFIX = "."
STAGING_SUFFIX = ".partial"


@dataclass(frozen=True, slots=True)
class _Staged:
    """A complete replacement waiting beside its destination, and what it cost."""

    path: Path
    stats: TreeStats


class Staging:
    """Puts a file or tree at a destination without ever leaving it half-written.

    Bound to the same guard and guarded walk the handlers use, so every path it
    creates, copies, or removes is confined and no walk it runs follows a symlink
    out of the jail. Callers are expected to have resolved and jailed both paths
    and settled the overwrite policy first; this owns only the *how*.
    """

    def __init__(self, guard: PathGuard, tree: GuardedTree, *, max_file_bytes: int) -> None:
        self._guard = guard
        self._tree = tree
        self._max_file_bytes = max_file_bytes

    def move(self, src: Path, dst: Path) -> bool:
        """Move ``src`` onto ``dst``; True if one atomic rename sufficed."""
        displaced = self._displace(dst)
        try:
            atomic = self._relocate(src, dst)
        except BaseException:
            self._restore(displaced, dst)
            raise
        self._discard(displaced)
        return atomic

    def copy(self, src: Path, dst: Path) -> TreeStats:
        """Copy ``src`` onto ``dst`` and report what the copy touched."""
        staged = self._stage(src, dst)
        displaced = self._displace(dst)
        try:
            self._install(staged.path, dst)
        except BaseException:
            self._restore(displaced, dst)
            raise
        self._discard(displaced)
        return staged.stats

    def _relocate(self, src: Path, dst: Path) -> bool:
        """Put ``src`` at ``dst``, which is free; True if one rename sufficed."""
        try:
            os.rename(src, dst)
            return True
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise

        # Different filesystems: no rename to be had, so copy under the guard and
        # remove the source only once the copy is verifiably complete. A failure
        # mid-copy leaves the source untouched and nothing at the destination, so
        # a failed move never loses data and never duplicates it either.
        staged = self._stage(src, dst)
        self._install(staged.path, dst)
        self.purge(src)
        return False

    def _stage(self, src: Path, dst: Path) -> _Staged:
        """Assemble a complete copy of ``src`` beside ``dst``, or leave nothing."""
        staging = self._staging_path(dst)
        try:
            if src.is_dir() and not src.is_symlink():
                stats = self._tree.copy_into(src, staging, max_file_bytes=self._max_file_bytes)
            else:
                stats = self._tree.copy_file(src, staging, max_file_bytes=self._max_file_bytes)
        except BaseException:
            self.purge(staging)
            raise
        return _Staged(path=staging, stats=stats)

    def _displace(self, dst: Path) -> Path | None:
        """Rename an existing ``dst`` aside, or None if there was nothing there."""
        if not dst.exists() and not dst.is_symlink():
            return None
        aside = self._staging_path(dst)
        os.rename(dst, aside)
        return aside

    def _restore(self, displaced: Path | None, dst: Path) -> None:
        """Put a displaced destination back after a failed replacement."""
        if displaced is None:
            return
        self.purge(dst)
        try:
            os.rename(displaced, dst)
        except OSError:
            return

    def _install(self, staging: Path, dst: Path) -> None:
        """Rename a completed staging tree into place at a free ``dst``."""
        try:
            os.rename(staging, dst)
        except BaseException:
            self.purge(staging)
            raise

    def _discard(self, displaced: Path | None) -> None:
        """Drop a displaced destination once its replacement is committed."""
        if displaced is not None:
            self.purge(displaced)

    def purge(self, path: Path) -> None:
        """Remove a tree this protocol staged or displaced, best-effort.

        Unbounded on purpose: the caps exist to bound what a *model* asked for,
        and a rollback that stopped at a cap because the failure *was* a cap
        would leave exactly the debris it was called to clear. Containment and
        the no-follow rule still apply. It never raises either — this is the
        recovery path, and failing to tidy up must not mask the original error.
        """
        try:
            if path.is_dir() and not path.is_symlink():
                self._tree.remove(path, bounded=False)
            else:
                path.unlink(missing_ok=True)
        except (OSError, PathBlocked):
            return

    def _staging_path(self, dst: Path) -> Path:
        """A free, dot-prefixed sibling of ``dst`` to assemble a replacement in.

        A sibling, so the final rename is same-directory and cannot fail for want
        of space on another filesystem.
        """
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%f")
        candidate = dst.parent / f"{STAGING_PREFIX}{dst.name}.{stamp}{STAGING_SUFFIX}"
        suffix = 2
        while candidate.exists():
            candidate = dst.parent / f"{STAGING_PREFIX}{dst.name}.{stamp}-{suffix}{STAGING_SUFFIX}"
            suffix += 1
        return candidate
