"""``BubblewrapSandbox`` — real filesystem and network isolation via ``bwrap``.

The recommended backend on Linux for code you actually do not trust. Where the
subprocess backend only *limits* the child, bubblewrap *isolates* it: the code
runs in a fresh mount and network namespace where the host root is visible
read-only, ``$HOME`` and ``~/.arcana`` are **not bound at all**, ``/tmp`` is a
private tmpfs, and the only writable path is the scratch workspace. With
``network=False`` the network namespace is unshared (``--unshare-net``) — no
route out exists, not merely a discouraged one.

It still runs on :func:`run_process`, so the same ``setrlimit`` ceilings,
process-group kill, and output caps apply on top of the namespace isolation —
bubblewrap does not bound memory or CPU by itself.

One external dependency (``bwrap``); absent, selection raises
:class:`SandboxUnavailable` and the tool degrades to disabled rather than
crashing.
"""

import shutil
import sys
from pathlib import Path

from arcana.tools.builtins.code.config import CodeLanguage
from arcana.tools.builtins.code.sandbox.base import ExecResult, SandboxUnavailable
from arcana.tools.builtins.code.sandbox.process import interpreter_argv, resource_limits, run_process

#: Host directories bound read-only so the interpreter and shared libraries
#: resolve. Bound with ``--ro-bind-try`` so one absent on a given distro (``/lib64``
#: on a pure-``/usr`` layout) is skipped rather than failing the whole launch.
#: ``$HOME`` and ``~/.arcana`` are pointedly not in this list.
_RO_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")


def build_argv(
    bwrap: str,
    inner_argv: list[str],
    *,
    workspace: Path,
    network: bool,
) -> list[str]:
    """Assemble the ``bwrap`` command that wraps ``inner_argv``.

    Kept a pure function so the isolation flags — the security surface of this
    backend — can be asserted in a test without a ``bwrap`` binary present.
    """
    argv = [bwrap, "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts"]
    if not network:
        argv.append("--unshare-net")
    for path in _RO_SYSTEM_DIRS:
        argv += ["--ro-bind-try", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    argv += ["--bind", str(workspace), str(workspace), "--chdir", str(workspace)]
    argv.append("--")
    argv += inner_argv
    return argv


class BubblewrapSandbox:
    """Namespace-isolated backend. Construct via the factory, which locates ``bwrap``."""

    name = "bubblewrap"

    def __init__(self, bwrap_path: str) -> None:
        self._bwrap = bwrap_path

    @classmethod
    def locate(cls) -> "BubblewrapSandbox":
        """Build the backend, or raise :class:`SandboxUnavailable` if ``bwrap`` is absent."""
        path = shutil.which("bwrap")
        if path is None:
            raise SandboxUnavailable("bubblewrap backend selected but 'bwrap' is not on PATH")
        return cls(path)

    async def run(
        self,
        code: str,
        *,
        language: CodeLanguage,
        timeout_s: float,
        mem_limit_mb: int,
        workspace: Path,
        env: dict[str, str],
        max_output_bytes: int,
        network: bool = False,
    ) -> ExecResult:
        inner = interpreter_argv(language, python=sys.executable)
        argv = build_argv(self._bwrap, inner, workspace=workspace, network=network)
        return await run_process(
            argv,
            stdin_data=code.encode("utf-8"),
            timeout_s=timeout_s,
            workspace=workspace,
            env=env,
            max_output_bytes=max_output_bytes,
            preexec_fn=resource_limits(timeout_s, mem_limit_mb),
        )
