"""Builtin **code-execution** tool: ``run_code``, behind a pluggable sandbox.

The single highest-blast-radius capability the OS ships, so its posture is the
inverse of the other builtins': **off by default**. The public surface is
:class:`CodeTools` (the handler the builtin adapter hosts), :class:`CodeToolsConfig`
(the enabled switch, backend, and caps), and the :class:`Sandbox` seam with its
backends. Isolation strength is a backend the operator chooses — a soft subprocess
jail by default, bubblewrap or an ephemeral container for actually-untrusted code.
"""

from arcana.tools.builtins.code.config import (
    CodeLanguage,
    CodeToolsConfig,
    CodeToolsTunables,
    SandboxBackend,
)
from arcana.tools.builtins.code.definitions import RUN_CODE
from arcana.tools.builtins.code.handlers import CodeTools
from arcana.tools.builtins.code.sandbox import (
    BubblewrapSandbox,
    ContainerSandbox,
    ExecResult,
    Sandbox,
    SandboxUnavailable,
    SubprocessSandbox,
    make_sandbox,
)

__all__ = [
    "RUN_CODE",
    "BubblewrapSandbox",
    "CodeLanguage",
    "CodeTools",
    "CodeToolsConfig",
    "CodeToolsTunables",
    "ContainerSandbox",
    "ExecResult",
    "Sandbox",
    "SandboxBackend",
    "SandboxUnavailable",
    "SubprocessSandbox",
    "make_sandbox",
]
