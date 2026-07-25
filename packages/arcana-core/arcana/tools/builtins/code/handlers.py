"""The code-execution builtin handler — ``run_code``, bound to one sandbox.

Composed into ``BuiltinToolAdapter``'s handler table, mirroring ``FsTools``: the
adapter owns routing and the never-raise envelope, this owns the execution
semantics. The handler is **always registered** so the tool's schema is offered,
but it is **default-disabled**: with no configuration it returns a typed error
and spawns nothing. Enabling it, and choosing the sandbox backend, is an operator
act settled once at construction.

Every failure path is a ``ToolResult(success=False)`` — disabled, an unavailable
backend, a rejected language, a missing argument, an OS error. A non-zero exit
code is the one thing that is **not** a failure: a program that errors produced a
*successful* tool call carrying its ``exit_code``, the same stance ``fetch_url``
takes on an HTTP 404.
"""

import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from arcana.observability import get_current_span
from arcana.tools.builtins.code.config import CodeLanguage, CodeToolsConfig
from arcana.tools.builtins.code.sandbox import Sandbox, SandboxUnavailable, make_sandbox
from arcana.types.tool import BuiltinTool, ToolResult

#: The language a call runs as when it names none. Python is always allowed if any
#: language is; a call that omits ``language`` gets the safe default.
_DEFAULT_LANGUAGE = CodeLanguage.PYTHON


def _scrubbed_env(workspace: Path) -> dict[str, str]:
    """The child's entire environment — built fresh, never inherited.

    A fixed, minimal allowlist rather than a filtered copy of the parent's, so no
    secret, keyring handle, or model API key in Arcana's own environment can leak
    into executed code. ``HOME`` and ``TMPDIR`` point at the scratch workspace, so
    code that reads ``~`` or writes a temp file stays inside the sandbox rather
    than reaching the real home.
    """
    return {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(workspace),
        "TMPDIR": str(workspace),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


class CodeTools:
    """The ``run_code`` builtin, bound to one sandbox backend.

    The sandbox is built once at construction, but only when the tool is enabled:
    a disabled tool never probes for a ``bwrap`` or container binary. A backend
    whose binary is absent leaves the tool disabled with a clear reason instead of
    failing construction — the fail-closed posture the whole slice turns on.
    """

    def __init__(self, config: CodeToolsConfig | None = None) -> None:
        self._cfg = config or CodeToolsConfig()
        self._sandbox: Sandbox | None = None
        self._disabled_reason: str | None = None
        if self._cfg.enabled:
            try:
                self._sandbox = make_sandbox(self._cfg)
            except SandboxUnavailable as unavailable:
                self._disabled_reason = str(unavailable)

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[ToolResult]]]:
        """This domain's single entry for the builtin adapter's handler table."""
        return {BuiltinTool.RUN_CODE: self.run_code}

    async def run_code(self, args: dict[str, Any]) -> ToolResult:
        """Execute a program in the sandbox and return its captured result.

        Fails closed before spawning anything on every guarded condition:
        disabled, an unavailable backend, a missing or non-string ``code``, or a
        language the operator did not allow. Only once past all of those is a
        process ever started.
        """
        tool = BuiltinTool.RUN_CODE
        if not self._cfg.enabled:
            return ToolResult(tool_name=tool, success=False, error="run_code is disabled")
        if self._sandbox is None:
            return ToolResult(tool_name=tool, success=False, error=f"run_code is disabled: {self._disabled_reason}")

        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(tool_name=tool, success=False, error="missing 'code'")

        language = self._resolve_language(args.get("language"))
        if language is None:
            allowed = ", ".join(lang.value for lang in self._cfg.languages)
            return ToolResult(tool_name=tool, success=False, error=f"unsupported language (allowed: {allowed})")

        timeout_s = self._resolve_timeout(args.get("timeout_s"))
        return await self._execute(language, code, timeout_s)

    async def _execute(self, language: CodeLanguage, code: str, timeout_s: float) -> ToolResult:
        """Run one call inside a fresh scratch workspace, cleaned up after.

        The workspace is a throwaway temp directory, distinct from the filesystem
        tools' agent workspace — code execution gets its own scratch, never a path
        into the agent's files or anywhere under ``~/.arcana``.
        """
        tool = BuiltinTool.RUN_CODE
        sandbox = self._sandbox
        assert sandbox is not None  # guarded by run_code; narrows for the type checker
        workspace = Path(tempfile.mkdtemp(prefix="arcana-run-code-"))
        try:
            result = await sandbox.run(
                code,
                language=language,
                timeout_s=timeout_s,
                mem_limit_mb=self._cfg.mem_limit_mb,
                workspace=workspace,
                env=_scrubbed_env(workspace),
                max_output_bytes=self._cfg.max_output_bytes,
                network=self._cfg.network,
            )
        except OSError as exc:
            # A backend that cannot even launch (binary vanished after selection,
            # a workspace it cannot enter) fails as a result, not an exception.
            return ToolResult(tool_name=tool, success=False, error=f"run_code failed to launch: {exc}")
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

        span = get_current_span()
        span.set_attribute("arcana.tool.run_code.backend", sandbox.name)
        span.set_attribute("arcana.tool.run_code.language", language.value)
        span.set_attribute("arcana.tool.run_code.exit_code", result.exit_code)
        span.set_attribute("arcana.tool.run_code.timed_out", result.timed_out)
        span.set_attribute("arcana.tool.run_code.output_bytes", len(result.stdout) + len(result.stderr))
        return ToolResult(
            tool_name=tool,
            success=True,
            output={
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
            },
        )

    def _resolve_language(self, requested: Any) -> CodeLanguage | None:
        """The allowed language for this call, or None if it is not permitted.

        An absent language defaults to Python; a named one must be both a known
        language and one the operator allowed. A default that is not in the
        allowed set (Python disabled, Bash-only) is itself refused rather than
        silently run.
        """
        if requested is None:
            language = _DEFAULT_LANGUAGE
        elif isinstance(requested, str):
            try:
                language = CodeLanguage(requested)
            except ValueError:
                return None
        else:
            return None
        return language if language in self._cfg.languages else None

    def _resolve_timeout(self, requested: Any) -> float:
        """Clamp a model-requested timeout to ``(0, configured maximum]``.

        The model may ask for a *shorter* run, never a longer one: the operator's
        ceiling is the hard bound, and a missing or invalid request just uses it.
        """
        ceiling = self._cfg.timeout_s
        if isinstance(requested, (int, float)) and not isinstance(requested, bool) and requested > 0:
            return min(float(requested), ceiling)
        return ceiling
