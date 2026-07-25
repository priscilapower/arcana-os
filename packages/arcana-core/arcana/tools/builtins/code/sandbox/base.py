"""The swappable ``run_code`` seam: execution result + sandbox protocol.

Isolation strength is a **backend choice the operator makes**, not a promise the
tool makes, so ``run_code`` talks to a :class:`Sandbox` rather than any one
mechanism — exactly as ``web_search`` talks to a ``SearchProvider``. Every
backend returns the same :class:`ExecResult`; the handler maps it to a
``ToolResult``. A backend whose external binary is absent raises
:class:`SandboxUnavailable` at selection, which the handler turns into a
"run_code disabled" result rather than a crash.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from arcana.tools.builtins.code.config import CodeLanguage, SandboxBackend


@dataclass(frozen=True, slots=True)
class ExecResult:
    """The outcome of one sandboxed run — what the model is shown.

    ``exit_code`` is data, not a verdict: a program that exits non-zero produced
    a *successful* tool call carrying that code, the same stance ``fetch_url``
    takes on an HTTP 404. ``timed_out`` marks a run the wall-clock killed;
    ``truncated`` marks output the byte cap cut, so the model is never misled
    into thinking it saw everything. On a timeout the streams hold whatever was
    captured before the kill — best-effort, never a guarantee.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    truncated: bool


class SandboxConfig(Protocol):
    """The backend-selection fields :func:`make_sandbox` reads to build a sandbox.

    A structural seam, not a base class: both ``CodeToolsConfig`` (``run_code``)
    and ``ShellToolsConfig`` (``run_command``) carry these fields, so the one
    factory builds a backend for either tool without importing either config —
    the sandbox stays shared across both tools rather than being copied for each.
    """

    backend: SandboxBackend
    container_command: str
    container_image: str


class SandboxUnavailable(Exception):
    """A selected backend cannot run here — its binary is missing, or the host
    lacks the primitives it needs.

    Raised at selection (never at import), so an operator who names
    ``bubblewrap`` or ``container`` without the binary installed gets a clean
    "run_code disabled" result instead of a stack trace mid-call.
    """


class Sandbox(Protocol):
    """A code-execution backend. ``name`` is a non-sensitive label for spans.

    The isolation each backend provides differs by design — a soft subprocess
    jail, a bubblewrap namespace, an ephemeral container — but they share one
    contract: run ``code`` to completion or to the wall-clock bound, capturing at
    most ``max_output_bytes`` of each stream, and never let the child reach the
    parent's environment. The environment the child sees is exactly ``env``;
    a backend never merges the parent's own environment into it. ``shell`` is the
    binary the ``SHELL`` language spawns (``run_command``'s ``bash -c`` path); it
    is ignored for the ``PYTHON``/``BASH`` languages.
    """

    name: str

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
        shell: str = "bash",
    ) -> ExecResult: ...
