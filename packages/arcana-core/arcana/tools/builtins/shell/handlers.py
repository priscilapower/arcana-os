"""The shell-command builtin handler — ``run_command``, bound to one sandbox.

Composed into ``BuiltinToolAdapter``'s handler table, mirroring ``CodeTools``: the
adapter owns routing and the never-raise envelope, this owns the execution
semantics. The handler is **always registered** so the tool's schema is offered,
but it is **default-disabled**: with no configuration it returns a typed error and
spawns nothing. Enabling it, choosing the sandbox backend, and pointing it at a
shell binary are operator acts settled once at construction.

Unlike ``run_code``'s throwaway scratch, ``run_command`` runs with the agent's
**jailed filesystem workspace** as its working directory, so a command's file
effects land in the same auditable directory the filesystem builtins use — honest
that cwd only *confines* the filesystem under the bubblewrap/container backends;
on the soft subprocess backend it is a convenience, not a boundary.

Every failure path is a ``ToolResult(success=False)`` — disabled, an unavailable
backend, a missing workspace, a missing argument, an OS error. A non-zero exit
code is the one thing that is **not** a failure: a command that errors produced a
*successful* tool call carrying its ``exit_code``, the same stance ``fetch_url``
takes on an HTTP 404. The ``DENY_PATTERN`` gate runs earlier, in the gateway seam,
so a blocked command never reaches this handler and nothing is spawned.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from arcana.observability import get_current_span
from arcana.tools.builtins.code.config import CodeLanguage
from arcana.tools.builtins.code.sandbox import Sandbox, SandboxUnavailable, make_sandbox
from arcana.tools.builtins.shell.config import ShellToolsConfig
from arcana.types.guardrails import GuardrailRule
from arcana.types.tool import BuiltinTool, ToolResult


def _scrubbed_env(path: str, workspace: Path) -> dict[str, str]:
    """The child's entire environment — built fresh, never inherited.

    A fixed, minimal allowlist rather than a filtered copy of the parent's, so no
    secret, keyring handle, or model API key in Arcana's own environment can leak
    into an executed command. ``PATH`` is the operator's controlled, minimal set
    of directories the shell resolves commands from — a mitigation, not a boundary.
    ``HOME`` and ``TMPDIR`` point at the workspace, so a command that reads ``~``
    or writes a temp file stays inside it rather than reaching the real home.
    """
    return {
        "PATH": path,
        "HOME": str(workspace),
        "TMPDIR": str(workspace),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


class ShellTools:
    """The ``run_command`` builtin, bound to one sandbox backend and one workspace.

    The sandbox is built once at construction, but only when the tool is enabled:
    a disabled tool never probes for a ``bwrap`` or container binary. A backend
    whose binary is absent leaves the tool disabled with a clear reason instead of
    failing construction. ``workspace`` is the agent's jailed filesystem workspace,
    used as the command's working directory; without one the tool is disabled, so
    an adapter built with no agent context cannot run a command anywhere.
    """

    def __init__(self, config: ShellToolsConfig | None = None, *, workspace: Path | None = None) -> None:
        self._cfg = config or ShellToolsConfig()
        self._workspace = workspace
        self._sandbox: Sandbox | None = None
        self._disabled_reason: str | None = None
        if self._cfg.enabled:
            try:
                self._sandbox = make_sandbox(self._cfg)
            except SandboxUnavailable as unavailable:
                self._disabled_reason = str(unavailable)

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[ToolResult]]]:
        """This domain's single entry for the builtin adapter's handler table."""
        return {BuiltinTool.RUN_COMMAND: self.run_command}

    def default_guardrails(self) -> tuple[GuardrailRule, ...]:
        """The baseline ``DENY_PATTERN`` rules the gateway screens ``run_command`` with.

        Sourced from the tool's own config so the shipped blocklist is always in
        force — even for an agent that carries no guardrails of its own — while
        World / agent rules add to it. Enforcement happens in the gateway seam, the
        single place ``DENY_PATTERN`` runs; this only supplies the rules.
        """
        return self._cfg.deny_pattern_rules()

    async def run_command(self, args: dict[str, Any]) -> ToolResult:
        """Run a shell command in the sandbox and return its captured result.

        Fails closed before spawning anything on every guarded condition:
        disabled, an unavailable backend, no workspace to run in, or a missing or
        non-string ``command``. Only once past all of those is a process started.
        """
        tool = BuiltinTool.RUN_COMMAND
        if not self._cfg.enabled:
            return ToolResult(tool_name=tool, success=False, error="run_command is disabled")
        if self._sandbox is None:
            return ToolResult(tool_name=tool, success=False, error=f"run_command is disabled: {self._disabled_reason}")
        if self._workspace is None:
            return ToolResult(tool_name=tool, success=False, error="run_command is disabled: no workspace")

        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(tool_name=tool, success=False, error="missing 'command'")

        timeout_s = self._resolve_timeout(args.get("timeout_s"))
        return await self._execute(command, timeout_s)

    async def _execute(self, command: str, timeout_s: float) -> ToolResult:
        """Run one command with the agent workspace as its working directory.

        The workspace is the shared, persistent jail the filesystem builtins use —
        never a throwaway — so effects accumulate in one auditable place and are
        left in place afterwards. It is created if absent, since a backend cannot
        ``chdir`` into a directory that does not exist yet.
        """
        tool = BuiltinTool.RUN_COMMAND
        sandbox = self._sandbox
        workspace = self._workspace
        assert sandbox is not None and workspace is not None  # guarded by run_command; narrows for the type checker
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            result = await sandbox.run(
                command,
                language=CodeLanguage.SHELL,
                timeout_s=timeout_s,
                mem_limit_mb=self._cfg.mem_limit_mb,
                workspace=workspace,
                env=_scrubbed_env(self._cfg.path, workspace),
                max_output_bytes=self._cfg.max_output_bytes,
                network=self._cfg.network,
                shell=self._cfg.shell,
            )
        except OSError as exc:
            # A backend that cannot even launch (binary vanished after selection,
            # a workspace it cannot enter) fails as a result, not an exception.
            return ToolResult(tool_name=tool, success=False, error=f"run_command failed to launch: {exc}")

        span = get_current_span()
        span.set_attribute("arcana.tool.run_command.backend", sandbox.name)
        span.set_attribute("arcana.tool.run_command.exit_code", result.exit_code)
        span.set_attribute("arcana.tool.run_command.timed_out", result.timed_out)
        span.set_attribute("arcana.tool.run_command.output_bytes", len(result.stdout) + len(result.stderr))
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

    def _resolve_timeout(self, requested: Any) -> float:
        """Clamp a model-requested timeout to ``(0, configured maximum]``.

        The model may ask for a *shorter* run, never a longer one: the operator's
        ceiling is the hard bound, and a missing or invalid request just uses it.
        """
        ceiling = self._cfg.timeout_s
        if isinstance(requested, (int, float)) and not isinstance(requested, bool) and requested > 0:
            return min(float(requested), ceiling)
        return ceiling
