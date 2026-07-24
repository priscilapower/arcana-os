"""The filesystem builtin handlers — ``read_file`` / ``write_file`` / ``delete_file``.

Each handler validates its arguments, puts the model-supplied path through the
shared :class:`~arcana.tools.builtins.fs.pathguard.PathGuard`, and returns a
populated ``ToolResult``. A refused path, an oversize payload, a missing file, or
any OS error comes back as ``ToolResult(success=False, error=…)`` fed to the
model — never as an exception, and never after touching the filesystem.

``delete_file`` is the defensive one: it refuses directories, and rather than
unlinking it moves the file into a timestamped, size-bounded ``.trash/`` inside
the same root, so an errant agent delete is recoverable. A real unlink is opt-in
via ``hard_delete``.
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
from arcana.types._utils import now_utc
from arcana.types.tool import BuiltinTool, ToolResult

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

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[ToolResult]]]:
        """This domain's entries for the builtin adapter's handler table."""
        return {
            BuiltinTool.LIST_DIR: self.list_dir,
            BuiltinTool.READ_FILE: self.read_file,
            BuiltinTool.WRITE_FILE: self.write_file,
            BuiltinTool.DELETE_FILE: self.delete_file,
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

        trash = self._guard.root_for(path) / TRASH_DIR_NAME
        # A file already in the trash has had its recoverable stage; deleting it
        # again means what it says.
        if self._cfg.hard_delete or is_within(path, trash):
            os.unlink(path)
            return {"path": str(path), "outcome": "deleted"}

        trash.mkdir(parents=True, exist_ok=True)
        destination = self._trash_destination(trash, path.name)
        os.replace(path, destination)
        self._prune_trash(trash)
        return {"path": str(path), "outcome": "trashed", "trash_path": str(destination)}

    @staticmethod
    def _trash_destination(trash: Path, name: str) -> Path:
        """A collision-free timestamped name for ``name`` inside ``trash``.

        Repeated deletes of the same filename must not overwrite each other in
        the trash — that would defeat the point of soft-deleting.
        """
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%f")
        candidate = trash / f"{stamp}-{name}"
        suffix = 2
        while candidate.exists():
            candidate = trash / f"{stamp}-{suffix}-{name}"
            suffix += 1
        return candidate

    def _prune_trash(self, trash: Path) -> None:
        """Drop the oldest trash entries past the configured bound.

        Soft-deleting trades disk for recoverability; this is what keeps that
        trade bounded. Ordering is by *name*, not mtime: the fixed-width
        timestamp prefix sorts chronologically by when the file was deleted,
        whereas ``os.replace`` carries the file's original mtime into the trash
        and would evict by when it was last written instead. Best-effort — a
        file that vanishes underneath the sweep (or cannot be removed) never
        fails the delete that triggered it.
        """
        try:
            entries = sorted(p for p in trash.iterdir() if p.is_file())
        except OSError:
            return
        for stale in entries[: max(0, len(entries) - self._cfg.trash_max_entries)]:
            try:
                stale.unlink()
            except OSError:
                continue

    def _span_attrs(self, tool: BuiltinTool, path: Path, *, bytes_: int | None = None) -> None:
        """Record the shape of the access on the current span — never its content."""
        span = get_current_span()
        span.set_attribute(f"arcana.tool.{tool}.root", str(self._guard.root_for(path)))
        if bytes_ is not None:
            span.set_attribute(f"arcana.tool.{tool}.bytes", bytes_)
