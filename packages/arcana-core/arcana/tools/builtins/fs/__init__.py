"""Builtin filesystem tools — ``read_file`` / ``write_file`` / ``delete_file``.

The public surface is :class:`FsTools` (the handlers the builtin adapter hosts)
and :class:`FsToolsConfig` (the jail roots and caps). :class:`PathGuard` is
exported for callers that need the same canonicalize-then-allowlist containment
outside a tool call.
"""

from arcana.tools.builtins.fs.config import FsToolsConfig, agent_workspace
from arcana.tools.builtins.fs.definitions import DELETE_FILE, LIST_DIR, READ_FILE, WRITE_FILE
from arcana.tools.builtins.fs.handlers import FsTools
from arcana.tools.builtins.fs.pathguard import (
    ListedEntry,
    PathBlocked,
    PathGuard,
    canonical_path,
    is_within,
)

__all__ = [
    "DELETE_FILE",
    "LIST_DIR",
    "READ_FILE",
    "WRITE_FILE",
    "FsTools",
    "FsToolsConfig",
    "ListedEntry",
    "PathBlocked",
    "PathGuard",
    "agent_workspace",
    "canonical_path",
    "is_within",
]
