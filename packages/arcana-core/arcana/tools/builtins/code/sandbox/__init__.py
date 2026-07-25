"""Pluggable code-execution backends behind the :class:`Sandbox` seam.

The default is :class:`SubprocessSandbox` — zero-dependency, a *soft* jail for
accidents and runaway loops. :class:`BubblewrapSandbox` and
:class:`ContainerSandbox` are opt-in upgrades that enforce real filesystem and
network isolation but need an external binary. :func:`make_sandbox` selects one
from :class:`CodeToolsConfig`; a backend whose binary is absent raises
:class:`SandboxUnavailable` here (at selection), which the handler turns into a
"run_code disabled" result rather than a crash mid-call.
"""

from arcana.tools.builtins.code.config import CodeToolsConfig, SandboxBackend
from arcana.tools.builtins.code.sandbox.base import ExecResult, Sandbox, SandboxUnavailable
from arcana.tools.builtins.code.sandbox.bubblewrap import BubblewrapSandbox
from arcana.tools.builtins.code.sandbox.container import ContainerSandbox
from arcana.tools.builtins.code.sandbox.process import POSIX
from arcana.tools.builtins.code.sandbox.subprocess import SubprocessSandbox

__all__ = [
    "BubblewrapSandbox",
    "ContainerSandbox",
    "ExecResult",
    "Sandbox",
    "SandboxUnavailable",
    "SubprocessSandbox",
    "make_sandbox",
]


def make_sandbox(config: CodeToolsConfig) -> Sandbox:
    """Build the configured backend, or raise :class:`SandboxUnavailable`.

    The subprocess default always builds on a POSIX host. The stronger backends
    probe for their binary and raise if it is missing, so the caller can degrade
    to a disabled tool with a clear reason instead of failing a call with a stack
    trace. On a non-POSIX host the local backends (subprocess, bubblewrap) have no
    ``setrlimit`` or process group to run behind and raise here too.
    """
    if config.backend is SandboxBackend.CONTAINER:
        return ContainerSandbox.locate(command=config.container_command, image=config.container_image)
    if not POSIX:
        raise SandboxUnavailable(f"backend '{config.backend}' requires a POSIX host")
    if config.backend is SandboxBackend.BUBBLEWRAP:
        return BubblewrapSandbox.locate()
    return SubprocessSandbox()
