"""``SubprocessSandbox`` — the zero-dependency default, honestly a *soft* jail.

Spawns the interpreter as a child under :func:`run_process` with three layers of
restraint applied in the child before exec:

* ``setrlimit`` bounds CPU seconds, address space (memory), the size of any file
  the code writes, and forbids core dumps — so a busy loop, an allocation bomb,
  or a write bomb hits a ceiling instead of the host;
* the child runs in a **fresh session**, so a wall-clock timeout takes down the
  whole process group (see :func:`run_process`);
* the interpreter runs **isolated** (``python -I -S`` / ``bash --noprofile
  --norc``) in a scratch ``workspace`` with a **scrubbed environment** the caller
  supplies — never the parent's — so no site config, startup file, secret, or
  API key in the parent's environment is reachable.

What it is honestly *not*: a defence against a determined attacker. It shares the
host kernel, uid, and (best-effort only) network — ``network=False`` is not
enforced here. For untrusted code, an operator selects the bubblewrap or
container backend, which enforce what this one can only limit.
"""

import sys
from pathlib import Path

from arcana.tools.builtins.code.config import CodeLanguage
from arcana.tools.builtins.code.sandbox.base import ExecResult
from arcana.tools.builtins.code.sandbox.process import program_invocation, resource_limits, run_process


class SubprocessSandbox:
    """Default backend: an isolated child with resource limits and a scrubbed env."""

    name = "subprocess"

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
    ) -> ExecResult:
        # network is accepted for protocol conformance; this backend cannot
        # enforce isolation, so it is deliberately not acted on here (documented
        # as best-effort, and the tool description says the default is soft).
        argv, stdin_data = program_invocation(language, code, python=sys.executable, shell=shell)
        return await run_process(
            argv,
            stdin_data=stdin_data,
            timeout_s=timeout_s,
            workspace=workspace,
            env=env,
            max_output_bytes=max_output_bytes,
            preexec_fn=resource_limits(timeout_s, mem_limit_mb),
        )
