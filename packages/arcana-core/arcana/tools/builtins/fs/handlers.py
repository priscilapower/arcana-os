"""The filesystem builtin handlers — the file set and the directory set.

Each handler validates its arguments, puts every model-supplied path through the
shared :class:`~arcana.tools.builtins.fs.pathguard.PathGuard`, and returns a
populated ``ToolResult``. A refused path, an oversize payload, a missing file, or
any OS error comes back as ``ToolResult(success=False, error=…)`` fed to the
model — never as an exception, and never after touching the filesystem.

The file handlers act on one path each. The directory handlers add three things
a single-file op never had to solve, and each is why a refusal here happens
*before* any mutation:

* ``move`` and ``copy`` take **two** model-chosen paths, and a traversal or
  symlink in either is an escape — so both are resolved and jailed up front, and
  a destination inside its own source is refused outright;
* ``copy`` and ``delete_dir`` **recurse**, where a followed symlink would turn a
  copy into exfiltration and a delete into a delete-anything primitive — so they
  descend through :class:`~arcana.tools.builtins.fs.tree.GuardedTree`, never
  ``shutil``;
* a tree operation **amplifies**, so the byte, entry, and depth caps are
  aggregate and are checked as the walk proceeds.

The deletes are the defensive ones: ``delete_file`` refuses directories,
``delete_dir`` refuses anything that is not one and refuses a jail root outright,
and rather than unlinking, both move their target into a timestamped,
size-bounded ``.trash/`` inside the same root, so an errant agent delete is
recoverable. A real unlink is opt-in via ``hard_delete``.
"""

import asyncio
import errno
import os
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from arcana.observability import get_current_span
from arcana.tools.builtins.fs.config import TRASH_DIR_NAME, FsToolsConfig
from arcana.tools.builtins.fs.pathguard import PathBlocked, PathGuard, is_within
from arcana.tools.builtins.fs.staging import Staging
from arcana.tools.builtins.fs.trash import Trash
from arcana.tools.builtins.fs.tree import GuardedTree, TreeCaps, TreeStats
from arcana.types.tool import BuiltinTool, DeleteOutcome, ToolResult

#: Accepted ``write_file`` modes. ``create`` refuses to clobber, so the default
#: is the non-destructive one — overwriting is something the model must ask for.
WRITE_MODES = ("create", "overwrite", "append")
DEFAULT_WRITE_MODE = "create"

#: What ``list_dir`` lists when given no path. Relative paths anchor to the
#: primary root, so this resolves to the workspace itself.
WORKSPACE_ROOT_ALIAS = "."

#: OS errors worth naming precisely for the model; anything else degrades to its
#: exception type, so a message never carries an unexpected payload.
_OS_ERROR_REASONS = {
    errno.ENOENT: "file not found",
    errno.EISDIR: "path is a directory",
    errno.ENOTDIR: "parent path is not a directory",
    errno.EACCES: "permission denied",
    errno.EPERM: "permission denied",
    errno.EEXIST: "file already exists",
    errno.ENOTEMPTY: "directory is not empty",
    errno.ENOSPC: "no space left on device",
}


def _blocked(tool: BuiltinTool, reason: str) -> ToolResult:
    """A refused call, phrased the way the egress guard phrases a blocked fetch."""
    return ToolResult(tool_name=tool, success=False, error=f"blocked: {reason}")


def _os_error(tool: BuiltinTool, exc: OSError) -> ToolResult:
    """Map an OS error to a short, model-actionable message."""
    return ToolResult(tool_name=tool, success=False, error=_OS_ERROR_REASONS.get(exc.errno or 0, type(exc).__name__))


class FsTools:
    """The filesystem builtins, bound to one path jail.

    Composed into ``BuiltinToolAdapter``'s handler table rather than inheriting
    from it: the adapter owns routing and the never-raise envelope, this owns
    filesystem semantics. Construct with a config whose ``allowed_roots`` name
    the agent's workspace — with no roots, every call fails closed.
    """

    def __init__(self, config: FsToolsConfig | None = None) -> None:
        self._cfg = config or FsToolsConfig()
        self._guard = PathGuard(self._cfg.allowed_roots, follow_symlinks=self._cfg.follow_symlinks)
        self._tree = GuardedTree(
            self._guard,
            TreeCaps(
                max_tree_bytes=self._cfg.max_tree_bytes,
                max_file_count=self._cfg.max_file_count,
                max_depth=self._cfg.max_depth,
            ),
        )
        self._staging = Staging(self._guard, self._tree, max_file_bytes=self._cfg.max_write_bytes)

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[ToolResult]]]:
        """This domain's entries for the builtin adapter's handler table."""
        return {
            BuiltinTool.LIST_DIR: self.list_dir,
            BuiltinTool.READ_FILE: self.read_file,
            BuiltinTool.WRITE_FILE: self.write_file,
            BuiltinTool.DELETE_FILE: self.delete_file,
            BuiltinTool.MAKE_DIR: self.make_dir,
            BuiltinTool.MOVE: self.move,
            BuiltinTool.COPY: self.copy,
            BuiltinTool.DELETE_DIR: self.delete_dir,
        }

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def list_dir(self, args: dict[str, Any]) -> ToolResult:
        """List one directory level inside the jail, capped and truncation-flagged.

        ``path`` is optional and defaults to the workspace root, so a model with
        no idea what it has can call this with no arguments — without it, an
        agent could only ever read paths it was told about.
        """
        tool = BuiltinTool.LIST_DIR
        raw = args.get("path") or WORKSPACE_ROOT_ALIAS
        try:
            path = self._guard.resolve(raw)
            entries, truncated = await asyncio.to_thread(self._guard.list_entries, path, self._cfg.max_list_entries)
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, path)
        span = get_current_span()
        span.set_attribute("arcana.tool.list_dir.count", len(entries))
        span.set_attribute("arcana.tool.list_dir.truncated", truncated)
        return ToolResult(
            tool_name=tool,
            success=True,
            output={
                "path": str(path),
                "entries": [{"name": e.name, "kind": e.kind, "size": e.size} for e in entries],
                "truncated": truncated,
            },
        )

    async def read_file(self, args: dict[str, Any]) -> ToolResult:
        """Read a UTF-8 text file from inside the jail, capped and truncation-flagged."""
        tool = BuiltinTool.READ_FILE
        try:
            path = self._guard.resolve(args.get("path"))
            data, truncated = await asyncio.to_thread(self._guard.read_bytes, path, self._cfg.max_read_bytes)
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Never hand raw bytes back to the model: say what happened instead.
            return ToolResult(
                tool_name=tool,
                success=False,
                error="unsupported: file is not valid UTF-8 text",
            )

        self._span_attrs(tool, path, bytes_=len(data))
        get_current_span().set_attribute("arcana.tool.read_file.truncated", truncated)
        return ToolResult(
            tool_name=tool,
            success=True,
            output={"path": str(path), "text": text, "truncated": truncated, "encoding": "utf-8"},
        )

    async def write_file(self, args: dict[str, Any]) -> ToolResult:
        """Write text into the jail atomically, honouring ``mode`` and the byte cap."""
        tool = BuiltinTool.WRITE_FILE
        content = args.get("content")
        if not isinstance(content, str):
            return ToolResult(tool_name=tool, success=False, error="missing 'content'")

        mode = args.get("mode", DEFAULT_WRITE_MODE)
        if mode not in WRITE_MODES:
            return ToolResult(
                tool_name=tool,
                success=False,
                error=f"invalid 'mode': expected one of {', '.join(WRITE_MODES)}",
            )

        try:
            path = self._guard.resolve(args.get("path"), for_write=True)
            payload = await asyncio.to_thread(self._compose_write, path, content, mode)
            await asyncio.to_thread(self._guard.atomic_write, path, payload)
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, path, bytes_=len(payload))
        get_current_span().set_attribute("arcana.tool.write_file.mode", mode)
        return ToolResult(
            tool_name=tool,
            success=True,
            output={"path": str(path), "bytes_written": len(payload), "mode": mode},
        )

    async def delete_file(self, args: dict[str, Any]) -> ToolResult:
        """Delete one file from inside the jail — to the trash unless hard delete is on."""
        tool = BuiltinTool.DELETE_FILE
        try:
            path = self._guard.resolve(args.get("path"), for_write=True)
            outcome = await asyncio.to_thread(self._remove, path)
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, path)
        get_current_span().set_attribute("arcana.tool.delete_file.outcome", outcome["outcome"])
        return ToolResult(tool_name=tool, success=True, output=outcome)

    async def make_dir(self, args: dict[str, Any]) -> ToolResult:
        """Create a directory inside the jail, optionally with its intermediates."""
        tool = BuiltinTool.MAKE_DIR
        try:
            path = self._guard.resolve(args.get("path"), for_write=True, allow_dir=True)
            created = await asyncio.to_thread(
                self._make_dir,
                path,
                parents=bool(args.get("parents", False)),
                exist_ok=bool(args.get("exist_ok", False)),
            )
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, path)
        get_current_span().set_attribute("arcana.tool.make_dir.created", created)
        return ToolResult(tool_name=tool, success=True, output={"path": str(path), "created": created})

    async def move(self, args: dict[str, Any]) -> ToolResult:
        """Move a file or a whole tree to another path inside the jail.

        A rename when source and destination share a filesystem — one atomic
        syscall, and the common case since both sit in the same workspace. Across
        filesystems there is no atomic rename to be had, so it degrades to a
        guarded copy followed by a verified delete of the source, and says so in
        the result rather than implying an atomicity it did not deliver.
        """
        tool = BuiltinTool.MOVE
        try:
            src, dst = self._resolve_pair(args)
            atomic = await asyncio.to_thread(self._move, src, dst, overwrite=bool(args.get("overwrite", False)))
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, dst)
        get_current_span().set_attribute("arcana.tool.move.atomic", atomic)
        return ToolResult(
            tool_name=tool,
            success=True,
            output={"src": str(src), "dst": str(dst), "moved": True, "atomic": atomic},
        )

    async def copy(self, args: dict[str, Any]) -> ToolResult:
        """Copy a file or a whole tree to another path inside the jail.

        A tree is built in a staging sibling of the destination and renamed into
        place only once it is complete, so a copy that trips a cap, meets an
        escaping symlink, or fails part-way leaves no half-built destination
        behind — and a pre-existing destination is only replaced once there is
        something whole to replace it with.
        """
        tool = BuiltinTool.COPY
        try:
            src, dst = self._resolve_pair(args)
            stats = await asyncio.to_thread(self._copy, src, dst, overwrite=bool(args.get("overwrite", False)))
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, dst, bytes_=stats.bytes)
        get_current_span().set_attribute("arcana.tool.copy.files", stats.files)
        return ToolResult(
            tool_name=tool,
            success=True,
            output={
                "src": str(src),
                "dst": str(dst),
                "files_copied": stats.files,
                "dirs_copied": stats.dirs,
                "bytes_copied": stats.bytes,
                "skipped": stats.skipped,
            },
        )

    async def delete_dir(self, args: dict[str, Any]) -> ToolResult:
        """Recursively delete a directory — to the trash unless hard delete is on.

        The most destructive builtin in the set, so it is the most defensive: it
        refuses a jail root (a workspace's *contents* can be emptied, the
        workspace itself cannot be removed out from under the jail), refuses
        anything that is not a directory, and walks the whole tree under the
        guard before touching it — so a tree that escapes or exceeds a cap is
        refused while it is still entirely intact.
        """
        tool = BuiltinTool.DELETE_DIR
        try:
            path = self._guard.resolve(args.get("path"), for_write=True, allow_dir=True)
            outcome = await asyncio.to_thread(self._remove_tree, path)
        except PathBlocked as blocked:
            return _blocked(tool, blocked.reason)
        except OSError as exc:
            return _os_error(tool, exc)

        self._span_attrs(tool, path)
        span = get_current_span()
        span.set_attribute("arcana.tool.delete_dir.outcome", str(outcome["outcome"]))
        span.set_attribute("arcana.tool.delete_dir.entry_count", int(outcome["entry_count"]))
        return ToolResult(tool_name=tool, success=True, output=outcome)

    # ------------------------------------------------------------------
    # Blocking internals — always called through asyncio.to_thread
    # ------------------------------------------------------------------

    def _compose_write(self, path: Path, content: str, mode: str) -> bytes:
        """The exact bytes to land at ``path``, with the cap enforced up front.

        Every refusal here happens before :meth:`PathGuard.atomic_write` opens
        anything, so an over-cap or clobbering write leaves the filesystem
        untouched. ``append`` reads the existing file first and refuses when it
        is already past the cap, so appending can never silently truncate it.
        """
        cap = self._cfg.max_write_bytes
        data = content.encode("utf-8")

        if mode == "create" and path.exists():
            raise PathBlocked("file already exists (use mode='overwrite')")

        if mode == "append" and path.exists():
            existing, truncated = self._guard.read_bytes(path, cap)
            if truncated:
                raise PathBlocked(f"existing file is larger than the {cap}-byte write cap")
            data = existing + data

        if len(data) > cap:
            raise PathBlocked(f"content is {len(data)} bytes, over the {cap}-byte write cap")
        return data

    def _remove(self, path: Path) -> dict[str, Any]:
        """Unlink or trash ``path``, returning the result payload.

        ``lstat`` rather than ``stat``: the path is already canonical, and the
        check refuses anything that is not a plain file (a socket, FIFO, or
        device node) before it is moved or unlinked.
        """
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            raise PathBlocked("file not found") from None
        if not stat.S_ISREG(mode):
            raise PathBlocked("not a regular file")

        trash = self._trash_for(path)
        # A file already in the trash has had its recoverable stage; deleting it
        # again means what it says.
        if self._cfg.hard_delete or is_within(path, trash.directory):
            os.unlink(path)
            return {"path": str(path), "outcome": DeleteOutcome.DELETED}

        return {"path": str(path), "outcome": DeleteOutcome.TRASHED, "trash_path": str(trash.place(path))}

    def _make_dir(self, path: Path, *, parents: bool, exist_ok: bool) -> bool:
        """Create ``path``; True if it was created, False if it already existed.

        ``parents`` is bounded rather than a plain ``makedirs``: the intermediate
        levels it would create are counted first and refused past the depth cap,
        so a pathological ``a/a/a/…`` cannot mint thousands of directories in one
        call.
        """
        if path.exists():
            if exist_ok and path.is_dir():
                return False
            raise PathBlocked("path already exists" if path.is_dir() else "path exists and is not a directory")

        if not parents:
            path.mkdir()
            return True

        self._make_parents(path)
        return True

    def _make_parents(self, path: Path) -> None:
        """Create ``path`` and its missing ancestors, bounded by the depth cap.

        The bound is the reason this is not a bare ``makedirs``: a single
        argument naming thousands of new levels is the same inode-amplification
        a recursive copy is capped against, and it costs just as little to ask
        for.
        """
        missing = self._missing_ancestors(path)
        if missing > self._cfg.max_depth:
            raise PathBlocked(f"path needs {missing} new levels, over the {self._cfg.max_depth}-level limit")
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _missing_ancestors(path: Path) -> int:
        """How many directory levels ``path`` would have to create to exist."""
        levels = 0
        for candidate in (path, *path.parents):
            if candidate.exists():
                break
            levels += 1
        return levels

    def _resolve_pair(self, args: dict[str, Any]) -> tuple[Path, Path]:
        """Resolve and jail both paths of a two-path call before anything moves.

        A two-path operation is only as confined as its weaker argument, so
        neither is trusted on the strength of the other, and a destination inside
        its own source is refused: a copy that recursed into its own growing
        output would never terminate. Both checks happen before a byte is
        touched, so a bad ``move`` or ``copy`` fails having changed nothing.
        """
        src = self._guard.resolve(args.get("src"), for_write=True, allow_dir=True)
        dst = self._guard.resolve(args.get("dst"), for_write=True, allow_dir=True)
        if is_within(dst, src):
            raise PathBlocked("destination is inside the source")
        return src, dst

    def _move(self, src: Path, dst: Path, *, overwrite: bool) -> bool:
        """Move ``src`` onto ``dst``; True if the move was a single atomic rename."""
        self._prepare_destination(src, dst, overwrite=overwrite)
        return self._staging.move(src, dst)

    def _copy(self, src: Path, dst: Path, *, overwrite: bool) -> TreeStats:
        """Copy ``src`` onto ``dst`` and report what the copy touched."""
        self._prepare_destination(src, dst, overwrite=overwrite)
        return self._staging.copy(src, dst)

    def _prepare_destination(self, src: Path, dst: Path, *, overwrite: bool) -> None:
        """Check ``src`` exists and that ``dst`` is free to be written.

        The overwrite policy, settled before any staging so the cheap refusals —
        a missing source, an occupied destination the caller did not ask to
        replace — cost nothing and change nothing. The destination's parents are
        created under the same depth bound ``make_dir`` uses: a ``copy`` into a
        thousand-level path is the same amplification whether the levels were
        asked for directly or implied by a destination.
        """
        if not src.exists() and not src.is_symlink():
            raise PathBlocked("source not found")
        if dst.exists() and not overwrite:
            raise PathBlocked("destination already exists (pass overwrite=true to replace it)")
        self._make_parents(dst.parent)

    def _remove_tree(self, path: Path) -> dict[str, Any]:
        """Trash or unlink the whole tree at ``path``, returning the result payload."""
        if self._guard.is_root(path):
            raise PathBlocked("cannot delete an allowed root")
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            raise PathBlocked("directory not found") from None
        # lstat, so a symlink *to* a directory is not mistaken for one: unlinking
        # it is delete_file's job, and following it is nobody's.
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise PathBlocked("not a directory")

        trash = self._trash_for(path)
        # The trash container is refused for the same reason a jail root is:
        # everything inside it can be removed, but removing it outright would
        # destroy every recoverable delete at once — and the recovery mechanism
        # with them.
        if path == trash.directory:
            raise PathBlocked("cannot delete the workspace trash itself")

        # Walk first. Nothing has moved yet, so an escaping or over-cap tree is
        # refused entirely intact.
        measured = self._tree.measure(path)
        payload: dict[str, Any] = {"path": str(path), "entry_count": measured.entries}

        # A tree already in the trash has had its recoverable stage; deleting it
        # again means what it says.
        if self._cfg.hard_delete or is_within(path, trash.directory):
            self._tree.remove(path)
            return {**payload, "outcome": DeleteOutcome.DELETED}

        return {**payload, "outcome": DeleteOutcome.TRASHED, "trash_path": str(trash.place(path))}

    def _trash_for(self, path: Path) -> Trash:
        """The trash belonging to the root ``path`` sits under.

        Placed beside the entry being deleted rather than in whichever root
        happens to be first, so a delete never moves data between roots.
        """
        return Trash(
            self._guard.root_for(path) / TRASH_DIR_NAME,
            max_entries=self._cfg.trash_max_entries,
            tree=self._tree,
        )

    def _span_attrs(self, tool: BuiltinTool, path: Path, *, bytes_: int | None = None) -> None:
        """Record the shape of the access on the current span — never its content."""
        span = get_current_span()
        span.set_attribute(f"arcana.tool.{tool}.root", str(self._guard.root_for(path)))
        if bytes_ is not None:
            span.set_attribute(f"arcana.tool.{tool}.bytes", bytes_)
