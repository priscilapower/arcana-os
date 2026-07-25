"""The workspace ``.trash/`` both delete tools land their target in.

Deleting is the one filesystem operation an agent cannot take back, so neither
``delete_file`` nor ``delete_dir`` unlinks by default: the target is *renamed*
into a timestamped entry under the workspace trash, which makes an errant delete
a recoverable mistake instead of a permanent one. A rename is also what keeps a
recursive delete cheap — a whole subtree moves in one syscall, however large it
is.

Recoverability is a trade against disk, so the trash is bounded: once a delete
pushes it past its entry limit the oldest entries are dropped. Pruning removes a
trashed *subtree* through the same guarded walk a live one goes through, so the
cleanup path cannot follow a symlink out of the jail either.
"""

import os
from pathlib import Path

from arcana.tools.builtins.fs.pathguard import PathBlocked
from arcana.tools.builtins.fs.tree import GuardedTree
from arcana.types._utils import now_utc


class Trash:
    """One workspace's trash directory, bounded to ``max_entries``."""

    def __init__(self, directory: Path, *, max_entries: int, tree: GuardedTree) -> None:
        self._dir = directory
        self._max_entries = max_entries
        self._tree = tree

    @property
    def directory(self) -> Path:
        return self._dir

    def place(self, path: Path) -> Path:
        """Move ``path`` — a file or a whole subtree — into the trash.

        Returns where it landed, so the result can tell the model exactly what to
        recover. Pruning runs afterwards: it is best-effort and must never fail
        the delete that triggered it.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        destination = self._destination(path.name)
        os.replace(path, destination)
        self._prune()
        return destination

    def _destination(self, name: str) -> Path:
        """A collision-free timestamped name for ``name`` inside the trash.

        Repeated deletes of the same filename must not overwrite each other —
        that would defeat the point of soft-deleting.
        """
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%f")
        candidate = self._dir / f"{stamp}-{name}"
        suffix = 2
        while candidate.exists():
            candidate = self._dir / f"{stamp}-{suffix}-{name}"
            suffix += 1
        return candidate

    def _prune(self) -> None:
        """Drop the oldest entries past the bound.

        Ordering is by *name*, not mtime: the fixed-width timestamp prefix sorts
        chronologically by when the entry was deleted, whereas ``os.replace``
        carries the original mtime into the trash and would evict by when it was
        last written instead. Best-effort throughout — an entry that vanishes
        underneath the sweep, or that cannot be removed, never fails the delete
        that triggered the sweep.

        The removal walk is deliberately **unbounded**: the tree caps bound what
        a model may ask a tool to touch, and applying them here would mean a
        trashed tree too large to walk could never be evicted — the trash would
        grow past its own limit precisely because something big landed in it.
        Containment and the no-follow rule still apply, so pruning still cannot
        reach outside the jail.
        """
        try:
            entries = sorted(self._dir.iterdir())
        except OSError:
            return
        for stale in entries[: max(0, len(entries) - self._max_entries)]:
            try:
                if stale.is_dir() and not stale.is_symlink():
                    self._tree.remove(stale, bounded=False)
                else:
                    stale.unlink()
            except (OSError, PathBlocked):
                continue
