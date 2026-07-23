"""Filesystem guardrails for reading and writing memory to user-supplied paths.

The memory commands' ``--vault`` / ``--path`` / ``--out`` flags are an attack
surface: an attacker-influenced path could read a folder or clobber a file
outside the user's own data. Everything here **fails closed** — a candidate path
is ``realpath``-resolved (symlinks collapsed) and confined to an allowed root
*before* any read or write, so a ``..`` escape, an absolute jump outside the
roots, or a symlink pointing outside is rejected up front, leaving no partial
file behind. Writes go through an atomic temp-then-rename and refuse to clobber
an existing target unless told to; the export size is capped.

The allowed roots default to the user's home directory and are overridable via
``ARCANA_MEMORY_SCOPE_PATHS`` (``os.pathsep``-separated), following the project's
env-override convention for tunable knobs; the export cap is
``ARCANA_MEMORY_MAX_FILE_MB``. Confinement lives here rather than in a caller so
every surface that resolves a memory path shares one implementation.
"""

import os
import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from arcana.memory.errors import PathSafetyError


class MemoryGuardrails(BaseSettings):
    """Filesystem-guardrail knobs, overridable via ``ARCANA_MEMORY_*`` env vars.

    Read once at import into the module constants below. ``scope_paths`` is an
    ``os.pathsep``-separated list of roots a memory path may live under; empty
    (the default) means "the user's home directory only". ``max_file_mb`` caps a
    single export file so a runaway dump can't fill the disk.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_MEMORY_", extra="ignore")

    scope_paths: str = ""
    max_file_mb: float = Field(default=50.0, gt=0.0)
    export_max_entries: int = Field(default=100_000, gt=0)


_GUARDRAILS = MemoryGuardrails()

#: Maximum size (bytes) of a single ``export --out`` file.
MAX_EXPORT_BYTES: int = int(_GUARDRAILS.max_file_mb * 1024 * 1024)

#: Upper bound on entries pulled per store when exporting — a dump is meant to be
#: whole, so this is high; the byte cap is the real backstop against a runaway file.
EXPORT_MAX_ENTRIES: int = _GUARDRAILS.export_max_entries


def allowed_roots() -> list[Path]:
    """The resolved roots a memory path may live under.

    Defaults to the user's home directory; ``ARCANA_MEMORY_SCOPE_PATHS`` replaces
    it with an explicit allowlist. Each root is ``realpath``-resolved so the
    confinement check compares symlink-free paths on both sides.
    """
    raw = [p for p in _GUARDRAILS.scope_paths.split(os.pathsep) if p.strip()]
    roots = raw or [str(Path.home())]
    return [Path(os.path.realpath(os.path.expanduser(r))) for r in roots]


def _confine(candidate: str | Path) -> Path:
    """Resolve ``candidate`` (symlinks collapsed) and confine it to an allowed root.

    ``realpath`` collapses ``..`` and every symlink — including a symlink whose
    target escapes the root and a non-existent leaf resolved against its real
    parent — so the confinement check cannot be fooled by a link or a relative
    hop. Raises :class:`PathSafetyError` when the resolved path sits under no
    allowed root.
    """
    resolved = Path(os.path.realpath(os.path.expanduser(str(candidate))))
    roots = allowed_roots()
    if any(resolved == root or resolved.is_relative_to(root) for root in roots):
        return resolved
    raise PathSafetyError(f"path is outside the allowed root(s): {candidate!r}")


def resolve_existing_dir(candidate: str | Path) -> Path:
    """Confine ``candidate`` and require it to be an existing, readable directory.

    The gate for ``connect --vault`` / ``--path``: the folder must already exist
    (a connector references a real source), be a directory, and be readable — all
    checked *after* confinement so no probe touches a path outside the roots.
    """
    resolved = _confine(candidate)
    if not resolved.exists():
        raise PathSafetyError(f"path does not exist: {candidate!r}")
    if not resolved.is_dir():
        raise PathSafetyError(f"path is not a directory: {candidate!r}")
    if not os.access(resolved, os.R_OK):
        raise PathSafetyError(f"path is not readable: {candidate!r}")
    return resolved


def resolve_out_path(candidate: str | Path) -> Path:
    """Confine an ``export --out`` target and require its parent directory to exist.

    Confines the path (so a symlinked or ``..``-escaping target is rejected before
    any write) and checks the parent is a real directory, but does not require the
    file itself to exist — it is about to be written. Clobber protection and the
    size cap are enforced by :func:`atomic_write_text`.
    """
    resolved = _confine(candidate)
    if resolved.is_dir():
        raise PathSafetyError(f"output path is a directory: {candidate!r}")
    if not resolved.parent.is_dir():
        raise PathSafetyError(f"output directory does not exist: {candidate!r}")
    return resolved


def atomic_write_text(path: Path, text: str, *, overwrite: bool, max_bytes: int = MAX_EXPORT_BYTES) -> None:
    """Write ``text`` to ``path`` atomically, capped and clobber-guarded.

    Refuses to overwrite an existing ``path`` unless ``overwrite`` is set, and
    rejects content larger than ``max_bytes`` — both before opening anything, so a
    refused write leaves the filesystem untouched. The bytes land in a temp file
    in the same directory and are ``os.replace``-d into place, so a reader never
    observes a half-written file and a mid-write failure never truncates an
    existing one. Assumes ``path`` has already been confined via
    :func:`resolve_out_path`.
    """
    if path.exists() and not overwrite:
        raise PathSafetyError(f"refusing to overwrite existing file without --yes: {path.name}")
    data = text.encode("utf-8")
    if len(data) > max_bytes:
        raise PathSafetyError(f"export is {len(data)} bytes, over the {max_bytes}-byte cap")

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
