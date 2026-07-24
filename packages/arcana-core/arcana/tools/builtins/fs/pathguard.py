"""The path-jail shared by the filesystem builtins — the FS analog of the egress guard.

``read_file`` / ``write_file`` / ``delete_file`` all take a path chosen by the
model, so a hallucinated or injected argument can point at ``../../../etc/passwd``,
``~/.ssh/id_rsa``, ``~/.arcana/secrets/…``, or a symlink aimed out of the intended
directory. This module is the always-on floor that makes those calls safe to *run*:

* **canonicalize, then allowlist** — ``realpath`` collapses ``..`` and every
  symlink, and only then is the real path tested for containment in an allowed
  root. A prefix-string check on the raw path would be defeated by either;
* **``O_NOFOLLOW`` on the leaf** — closes the TOCTOU window where a symlink is
  swapped in between canonicalization and the actual open;
* **regular files only** — a FIFO or device node is refused (and the probe is
  non-blocking, so a FIFO cannot wedge the call before the check runs);
* **byte caps** — reads stop at the cap and are flagged truncated; oversize
  writes are refused before a byte reaches the disk;
* **atomic writes** — content lands in a temp file in the same directory and is
  ``os.replace``-d into place, so a crash never leaves a half-written file and
  never truncates the original.

Everything fails *closed* by raising :class:`PathBlocked`, which the caller turns
into a ``ToolResult(success=False, …)`` — no descriptor is opened and nothing is
written until the checks pass. With no allowed roots at all, every path is
refused; widening is operator configuration, never a tool argument.
"""

import errno
import os
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class PathBlocked(Exception):
    """A path was refused before any filesystem access. ``reason`` is model-safe."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ListedEntry:
    """One entry in a directory listing, classified without following symlinks.

    ``kind`` is ``file``, ``dir``, ``symlink``, or ``other`` (a FIFO, socket, or
    device node). A symlink keeps its own identity rather than reporting its
    target's, so a listing never implies the jail reaches further than it does.
    """

    name: str
    kind: str
    size: int


def _describe(entry: os.DirEntry[str]) -> ListedEntry:
    """Classify a scanned entry by its own ``lstat``, never its target's."""
    try:
        mode = entry.stat(follow_symlinks=False).st_mode
        size = entry.stat(follow_symlinks=False).st_size
    except OSError:
        # Vanished mid-scan, or unreadable — name it and move on.
        return ListedEntry(name=entry.name, kind="other", size=0)

    if stat.S_ISLNK(mode):
        kind = "symlink"
    elif stat.S_ISDIR(mode):
        kind = "dir"
    elif stat.S_ISREG(mode):
        kind = "file"
    else:
        kind = "other"
    return ListedEntry(name=entry.name, kind=kind, size=size)


def canonical_path(raw: str | Path) -> Path:
    """Expand ``~``, collapse ``..`` and every symlink, and return the real path.

    ``realpath`` resolves a non-existent leaf against its real parent, so a path
    that is about to be written is canonicalized on the same terms as one being
    read. Raises :class:`PathBlocked` for a path the OS cannot even represent
    (an embedded NUL, say) rather than letting ``ValueError`` escape.
    """
    try:
        return Path(os.path.realpath(os.path.expanduser(str(raw))))
    except ValueError as exc:
        raise PathBlocked("invalid path") from exc


def is_within(path: Path, root: Path) -> bool:
    """True if ``path`` is ``root`` itself or sits underneath it.

    Both sides are expected to be canonical already, so this is a pure
    path-segment comparison — never a string prefix test, which would treat
    ``/home/user-other`` as inside ``/home/user``.
    """
    return path == root or path.is_relative_to(root)


class PathGuard:
    """Confines filesystem access to an allowlist of canonical roots.

    Construct with the roots a caller may reach; every path an agent supplies
    goes through :meth:`resolve` before any I/O. The guard owns the *safety* of
    an access — containment, symlink handling, file type, byte caps, write
    atomicity — and leaves tool semantics (modes, trash, result shape) to its
    caller.
    """

    def __init__(self, roots: Sequence[Path], *, follow_symlinks: bool = False) -> None:
        # Roots are canonicalized once here so containment compares symlink-free
        # paths on both sides — a root reached through a symlink still matches.
        self._roots = [canonical_path(root) for root in roots]
        self._follow_symlinks = follow_symlinks

    @property
    def roots(self) -> list[Path]:
        """The canonical roots this guard confines access to."""
        return list(self._roots)

    def resolve(self, raw: object, *, for_write: bool = False) -> Path:
        """Canonicalize ``raw`` and confine it to an allowed root.

        A relative path is anchored to the **primary root** — the agent's own
        workspace — not to the process working directory. A model writing
        ``notes.md`` means "in my workspace", and resolving that against whatever
        directory the process happened to start in would be both surprising and
        a way to reach files the jail is meant to exclude. Anchoring changes
        nothing about safety: the anchored path is canonicalized and contained
        exactly like an absolute one, so ``../../etc/passwd`` still escapes and
        is still refused.

        Returns the real path to act on. Raises :class:`PathBlocked` when the
        argument is not a usable path, when no root is configured at all, when
        the canonical path escapes every root, or — for a write — when it names
        an existing directory.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise PathBlocked("missing 'path'")
        if not self._roots:
            raise PathBlocked("no allowed roots configured")

        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            candidate = self._roots[0] / candidate
        resolved = canonical_path(candidate)
        if not any(is_within(resolved, root) for root in self._roots):
            raise PathBlocked("path outside allowed roots")
        if for_write and resolved.is_dir():
            raise PathBlocked("path is a directory")
        return resolved

    def root_for(self, path: Path) -> Path:
        """The allowed root containing ``path`` — the first one that matches.

        Used to place workspace-relative artifacts (the trash) beside the file
        being acted on rather than in whichever root happens to be first.
        """
        for root in self._roots:
            if is_within(path, root):
                return root
        raise PathBlocked("path outside allowed roots")

    def read_bytes(self, path: Path, max_bytes: int) -> tuple[bytes, bool]:
        """Read up to ``max_bytes`` from ``path``; the second value flags truncation.

        Opens with ``O_NOFOLLOW`` (unless symlinks are explicitly allowed) so a
        symlink swapped in after :meth:`resolve` cannot redirect the read, and
        refuses anything that is not a regular file.
        """
        fd = self._open_checked(path, os.O_RDONLY)
        with os.fdopen(fd, "rb", closefd=True) as handle:
            # One byte past the cap distinguishes "exactly at the cap" from
            # "there was more" without reading the whole file.
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            return data[:max_bytes], True
        return data, False

    def atomic_write(self, path: Path, data: bytes) -> None:
        """Write ``data`` to ``path`` atomically, creating parent directories.

        The parent is created only after :meth:`resolve` has confined ``path``,
        so directory creation cannot walk out of the jail. The bytes land in a
        temp file in the same directory (so ``os.replace`` is a same-filesystem
        rename) and are moved into place in one step: a reader never sees a
        half-written file, and a failure mid-write leaves the original intact.
        ``os.replace`` acts on the symlink itself rather than its target, so a
        write cannot be redirected through one.
        """
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def list_entries(self, path: Path, max_entries: int) -> tuple[list[ListedEntry], bool]:
        """List one directory level; the second value flags truncation.

        Enumerates through an ``O_NOFOLLOW`` **directory** descriptor, so the
        same swapped-symlink race :meth:`read_bytes` closes is closed here too,
        and each entry is classified with ``lstat`` — a symlink is reported *as*
        a symlink rather than followed, so a link out of the jail is described
        but never traversed. Names are sorted for a stable, diffable listing.
        Non-recursive: subdirectories are named, never descended into.
        """
        fd = self._open_checked(path, os.O_RDONLY, directory=True)
        try:
            entries = sorted(os.scandir(fd), key=lambda entry: entry.name)
            listed = [_describe(entry) for entry in entries[:max_entries]]
        finally:
            os.close(fd)
        return listed, len(entries) > max_entries

    def _open_checked(self, path: Path, flags: int, *, directory: bool = False) -> int:
        """Open ``path`` with the symlink and file-type checks applied.

        ``O_NONBLOCK`` is set for the open itself so a FIFO cannot block waiting
        for a peer *before* the type check rejects it; it is harmless on the
        regular files and directories that survive that check. ``directory``
        flips the expected type — the kernel enforces it via ``O_DIRECTORY`` and
        the ``fstat`` check backs that up.
        """
        if not self._follow_symlinks:
            flags |= os.O_NOFOLLOW
        if directory:
            flags |= os.O_DIRECTORY
        try:
            fd = os.open(path, flags | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise PathBlocked("symlink escape") from exc
            if directory and exc.errno == errno.ENOTDIR:
                # O_DIRECTORY rejects a non-directory before the fstat check can
                # phrase it; say what was actually wrong with the argument.
                raise PathBlocked("not a directory") from exc
            raise

        try:
            mode = os.fstat(fd).st_mode
            if directory and not stat.S_ISDIR(mode):
                raise PathBlocked("not a directory")
            if not directory and not stat.S_ISREG(mode):
                raise PathBlocked("not a regular file")
        except BaseException:
            os.close(fd)
            raise
        return fd
