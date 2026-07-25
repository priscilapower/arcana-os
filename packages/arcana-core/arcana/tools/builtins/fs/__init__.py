"""Builtin filesystem tools — the file set and the directory set.

The public surface is :class:`FsTools` (the handlers the builtin adapter hosts)
and :class:`FsToolsConfig` (the jail roots and caps). :class:`PathGuard` is
exported for callers that need the same canonicalize-then-allowlist containment
outside a tool call, and :class:`GuardedTree` for those that need it applied
across a whole subtree.
"""

from arcana.tools.builtins.fs.config import FsToolsConfig, agent_workspace
from arcana.tools.builtins.fs.definitions import (
    COPY,
    DELETE_DIR,
    DELETE_FILE,
    LIST_DIR,
    MAKE_DIR,
    MOVE,
    READ_FILE,
    WRITE_FILE,
)
from arcana.tools.builtins.fs.handlers import FsTools
from arcana.tools.builtins.fs.pathguard import (
    ListedEntry,
    PathBlocked,
    PathGuard,
    canonical_path,
    is_within,
)
from arcana.tools.builtins.fs.staging import Staging
from arcana.tools.builtins.fs.trash import Trash
from arcana.tools.builtins.fs.tree import GuardedTree, TreeCaps, TreeStats, WalkEntry

__all__ = [
    "COPY",
    "DELETE_DIR",
    "DELETE_FILE",
    "LIST_DIR",
    "MAKE_DIR",
    "MOVE",
    "READ_FILE",
    "WRITE_FILE",
    "FsTools",
    "FsToolsConfig",
    "GuardedTree",
    "ListedEntry",
    "PathBlocked",
    "PathGuard",
    "Staging",
    "Trash",
    "TreeCaps",
    "TreeStats",
    "WalkEntry",
    "agent_workspace",
    "canonical_path",
    "is_within",
]
