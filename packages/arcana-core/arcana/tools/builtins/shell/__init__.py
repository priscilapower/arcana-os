"""Builtin **shell-command** tool: ``run_command``, behind the shared sandbox.

The shell sibling of ``run_code``: a command string runs behind the same
pluggable :class:`~arcana.tools.builtins.code.sandbox.Sandbox` and its three
backends, reused verbatim. Its posture is the most conservative the OS ships —
**off by default**, denied by read-only cards, confirmation-gated for executing
ones — because a shell string is the most injection-shaped argument the OS
accepts and, unlike Python, has no language-level isolation lever: the sandbox
backend does all the confinement.

The public surface is :class:`ShellTools` (the handler the builtin adapter hosts),
:class:`ShellToolsConfig` (the enabled switch, backend, shell binary, and caps),
and :data:`DEFAULT_DENY_PATTERNS` (the coarse, deliberately-incomplete
``DENY_PATTERN`` baseline — a tripwire over the sandbox, never the boundary).
"""

from arcana.tools.builtins.shell.config import (
    DEFAULT_DENY_PATTERNS,
    ShellToolsConfig,
    ShellToolsTunables,
)
from arcana.tools.builtins.shell.definitions import RUN_COMMAND
from arcana.tools.builtins.shell.handlers import ShellTools

__all__ = [
    "DEFAULT_DENY_PATTERNS",
    "RUN_COMMAND",
    "ShellTools",
    "ShellToolsConfig",
    "ShellToolsTunables",
]
