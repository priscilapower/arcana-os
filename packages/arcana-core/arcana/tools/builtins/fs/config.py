"""Configuration for the builtin **filesystem** tools.

The byte caps, the symlink and hard-delete switches, and the trash bound ship
with safe defaults but are **overridable via environment variables**, so an
operator can tune filesystem access without waiting for a release.
:class:`FsToolsTunables` is a ``pydantic-settings`` model: it reads
``ARCANA_TOOLS_FS_*`` env vars once at import (typed, coerced, and
bounds-validated — a malformed value fails fast) and seeds the module constants
below.

:class:`FsToolsConfig` is the per-adapter object those constants default. The one
value a tool argument must never influence is ``allowed_roots``: it is operator
config plus the agent's own workspace, so a prompt-injected path cannot widen the
jail. Roots default to ``~/.arcana/agents/{id}/workspace`` alone — never
``~/.arcana`` itself, so secrets, connection configs, and other agents' memory sit
outside every default root.
"""

import os
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Per-agent directory the filesystem tools are jailed to, under the agent's home.
WORKSPACE_DIR_NAME = "workspace"

#: Where ``delete_file`` moves a file instead of unlinking it, inside the workspace.
TRASH_DIR_NAME = ".trash"


class FsToolsTunables(BaseSettings):
    """Filesystem-tool knobs, overridable via ``ARCANA_TOOLS_FS_*`` env vars.

    Seeds :class:`FsToolsConfig`'s field defaults. The ``_FS_`` segment keeps
    these distinct from the web-tool and tool-loop knobs, which share the
    broader ``ARCANA_TOOLS_`` prefix.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_TOOLS_FS_", extra="ignore")

    # Extra roots the filesystem tools may reach, os.pathsep-separated. Empty
    # (the default) means the agent workspace is the only root. Operator config
    # only — this is how a project directory is opened up deliberately.
    allowed_roots: str = ""

    # Read/write byte caps. A read past the cap is truncated and flagged; a write
    # past it is refused before any byte reaches the disk.
    max_read_bytes: int = Field(default=5 * 1024 * 1024, gt=0)  # 5 MiB
    max_write_bytes: int = Field(default=5 * 1024 * 1024, gt=0)  # 5 MiB

    # Off by default: the leaf of every path is opened with O_NOFOLLOW, so a
    # symlink swapped in after the path was canonicalized cannot redirect the I/O.
    follow_symlinks: bool = False

    # Off by default: delete_file moves the file into the workspace trash instead
    # of unlinking it, so an errant agent delete stays recoverable.
    hard_delete: bool = False

    # Entries returned by one list_dir. A listing past this is truncated and
    # flagged, so an enormous directory cannot flood the model's context.
    max_list_entries: int = Field(default=1000, gt=0)

    # Bound on the trash directory. The oldest entries are pruned once a delete
    # pushes the count past this, so soft-deletes cannot grow without limit.
    trash_max_entries: int = Field(default=100, gt=0)


FS_TOOLS_TUNABLES = FsToolsTunables()

DEFAULT_FS_EXTRA_ROOTS = FS_TOOLS_TUNABLES.allowed_roots
DEFAULT_MAX_READ_BYTES = FS_TOOLS_TUNABLES.max_read_bytes
DEFAULT_MAX_WRITE_BYTES = FS_TOOLS_TUNABLES.max_write_bytes
DEFAULT_FOLLOW_SYMLINKS = FS_TOOLS_TUNABLES.follow_symlinks
DEFAULT_HARD_DELETE = FS_TOOLS_TUNABLES.hard_delete
DEFAULT_MAX_LIST_ENTRIES = FS_TOOLS_TUNABLES.max_list_entries
DEFAULT_TRASH_MAX_ENTRIES = FS_TOOLS_TUNABLES.trash_max_entries


def _split_roots(raw: str) -> list[Path]:
    """Parse an ``os.pathsep``-separated root list, dropping blank entries."""
    return [Path(part.strip()) for part in raw.split(os.pathsep) if part.strip()]


def agent_workspace(agent_id: UUID, *, home: Path | None = None) -> Path:
    """The workspace directory the filesystem tools jail an agent to.

    Sits beside the agent's ``agent.json`` and ``memory.db`` rather than
    replacing them: the workspace is the only part of the agent's home the
    filesystem tools can reach.
    """
    root = home or (Path.home() / ".arcana")
    return root / "agents" / str(agent_id) / WORKSPACE_DIR_NAME


class FsToolsConfig(BaseModel):
    """Per-adapter configuration for the builtin filesystem tools.

    Field defaults come from the env-overridable tunables above; build with
    :meth:`for_agent` to get the default-closed per-agent jail. An empty
    ``allowed_roots`` denies every path — the fail-closed posture for an adapter
    constructed with no agent context and no operator roots.
    """

    allowed_roots: list[Path] = []
    max_read_bytes: int = Field(default=DEFAULT_MAX_READ_BYTES, gt=0)
    max_write_bytes: int = Field(default=DEFAULT_MAX_WRITE_BYTES, gt=0)
    follow_symlinks: bool = DEFAULT_FOLLOW_SYMLINKS
    hard_delete: bool = DEFAULT_HARD_DELETE
    max_list_entries: int = Field(default=DEFAULT_MAX_LIST_ENTRIES, gt=0)
    trash_max_entries: int = Field(default=DEFAULT_TRASH_MAX_ENTRIES, gt=0)

    @classmethod
    def for_agent(cls, agent_id: UUID | None, *, home: Path | None = None) -> "FsToolsConfig":
        """Build the default-closed config for one agent.

        The agent's workspace is the first root, followed by any operator roots
        from ``ARCANA_TOOLS_FS_ALLOWED_ROOTS``. Without an agent only the
        operator roots apply, so an adapter built with no agent context and no
        configured roots can reach nothing at all.
        """
        roots = [agent_workspace(agent_id, home=home)] if agent_id is not None else []
        roots.extend(_split_roots(DEFAULT_FS_EXTRA_ROOTS))
        return cls(allowed_roots=roots)
