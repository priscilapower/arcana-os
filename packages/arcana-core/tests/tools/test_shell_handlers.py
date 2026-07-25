"""The ``run_command`` handler — the fail-closed envelope around the shell sandbox.

The disabled, no-workspace, and validation paths never reach a sandbox, and the
"nothing spawned" guarantee is asserted by spying on the subprocess spawn itself.
The executing paths use the real (local) subprocess backend, running actual
``bash -c`` commands in a scratch workspace — hermetic (no network, no service).
"""

from pathlib import Path
from typing import Any

import pytest

from arcana.tools.builtins.code.config import SandboxBackend
from arcana.tools.builtins.shell.config import ShellToolsConfig
from arcana.tools.builtins.shell.handlers import ShellTools


def _enabled(workspace: Path, **overrides: Any) -> ShellTools:
    return ShellTools(ShellToolsConfig(enabled=True, **overrides), workspace=workspace)


# ---------------------------------------------------------------------------
# Fail-closed: disabled, no workspace, bad arguments — nothing runs
# ---------------------------------------------------------------------------


async def test_disabled_by_default_returns_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # NFR1: with no config the tool is off and no process is ever spawned. Spy on
    # the spawn primitive to prove the second half.
    async def _spy(*_args: Any, **_kwargs: Any):
        raise AssertionError("a disabled run_command must not spawn a process")

    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.process.asyncio.create_subprocess_exec", _spy)

    result = await ShellTools(ShellToolsConfig(), workspace=tmp_path).run_command({"command": "echo hi"})

    assert result.success is False
    assert result.error == "run_command is disabled"


async def test_a_missing_binary_leaves_the_tool_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.bubblewrap.shutil.which", lambda _: None)
    tools = ShellTools(ShellToolsConfig(enabled=True, backend=SandboxBackend.BUBBLEWRAP), workspace=tmp_path)

    result = await tools.run_command({"command": "echo hi"})

    assert result.success is False
    assert result.error is not None and "disabled" in result.error and "bwrap" in result.error


async def test_without_a_workspace_the_tool_is_disabled(monkeypatch: pytest.MonkeyPatch):
    # cwd is the agent's jailed workspace; with no agent context there is none, so
    # the tool refuses rather than running a command in some arbitrary directory.
    async def _spy(*_args: Any, **_kwargs: Any):
        raise AssertionError("a workspace-less run_command must not spawn a process")

    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.process.asyncio.create_subprocess_exec", _spy)

    result = await ShellTools(ShellToolsConfig(enabled=True), workspace=None).run_command({"command": "echo hi"})

    assert result.success is False
    assert result.error is not None and "workspace" in result.error


async def test_missing_command_is_refused(tmp_path: Path):
    result = await _enabled(tmp_path).run_command({})
    assert result.success is False
    assert result.error is not None and "command" in result.error


async def test_a_blank_command_is_refused(tmp_path: Path):
    result = await _enabled(tmp_path).run_command({"command": "   "})
    assert result.success is False


# ---------------------------------------------------------------------------
# The executing path — real bash -c
# ---------------------------------------------------------------------------


async def test_enabled_run_returns_the_structured_result(tmp_path: Path):
    result = await _enabled(tmp_path).run_command({"command": "echo hello"})
    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["stdout"].strip() == "hello"
    assert result.output["exit_code"] == 0
    assert result.output["timed_out"] is False
    assert result.output["truncated"] is False


async def test_a_non_bash_shell_runs_with_a_plain_dash_c(tmp_path: Path):
    # The bash-only --noprofile/--norc flags are dropped for another shell, so a
    # POSIX sh runs the command via a plain -c instead of erroring on the flags.
    result = await _enabled(tmp_path, shell="sh").run_command({"command": "echo via-sh"})
    assert result.success is True
    assert result.output["stdout"].strip() == "via-sh"
    assert result.output["exit_code"] == 0


async def test_a_pipeline_of_commands_runs(tmp_path: Path):
    # The whole reason for a shell tool: pipes and multiple programs in one string.
    result = await _enabled(tmp_path).run_command({"command": "printf 'a\\nb\\nc\\n' | grep b"})
    assert result.success is True
    assert result.output["stdout"].strip() == "b"


async def test_a_nonzero_exit_is_a_success_carrying_the_code(tmp_path: Path):
    # A failing command is data the model reads, not a tool failure.
    result = await _enabled(tmp_path).run_command({"command": "exit 3"})
    assert result.success is True
    assert result.output["exit_code"] == 3


async def test_stderr_is_captured_separately(tmp_path: Path):
    result = await _enabled(tmp_path).run_command({"command": "echo oops 1>&2"})
    assert result.success is True
    assert "oops" in result.output["stderr"]
    assert result.output["stdout"].strip() == ""


async def test_the_command_runs_in_the_agent_workspace(tmp_path: Path):
    workspace = tmp_path / "ws"
    result = await _enabled(workspace).run_command({"command": "pwd"})
    assert result.success is True
    # The workspace is created if absent and is the working directory.
    assert result.output["stdout"].strip() == str(workspace)
    assert workspace.is_dir()


async def test_file_effects_land_in_the_workspace(tmp_path: Path):
    # run_command shares the filesystem builtins' workspace, so what it writes is
    # there afterwards (no throwaway scratch, unlike run_code).
    result = await _enabled(tmp_path).run_command({"command": "echo data > artifact.txt"})
    assert result.success is True
    assert (tmp_path / "artifact.txt").read_text().strip() == "data"


async def test_the_child_environment_holds_no_host_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # NFR2: the command gets exactly the scrubbed env, never the parent's — a
    # secret in Arcana's own environment must be unreachable, and PATH is the
    # controlled minimal set.
    monkeypatch.setenv("ARCANA_SUPER_SECRET", "do-not-leak")
    command = "echo secret=$ARCANA_SUPER_SECRET; echo path=$PATH"
    result = await _enabled(tmp_path, path="/usr/bin:/bin").run_command({"command": command})
    lines = result.output["stdout"].splitlines()
    assert lines[0] == "secret="  # the parent's secret did not leak in
    assert lines[1] == "path=/usr/bin:/bin"  # only the controlled PATH


async def test_a_timeout_kills_the_command_and_flags_it(tmp_path: Path):
    result = await _enabled(tmp_path, timeout_s=0.5).run_command({"command": "sleep 30"})
    assert result.success is True
    assert result.output["timed_out"] is True


async def test_a_backgrounded_child_is_killed_with_the_group(tmp_path: Path):
    # A command that backgrounds a long sleep is taken down with the whole process
    # group on timeout, not left leaking.
    import time as _time

    started = _time.monotonic()
    result = await _enabled(tmp_path, timeout_s=0.5).run_command({"command": "sleep 100 & echo started"})
    assert result.output["timed_out"] is True
    assert _time.monotonic() - started < 10


async def test_output_over_the_cap_is_truncated(tmp_path: Path):
    result = await _enabled(tmp_path, max_output_bytes=1000).run_command({"command": "yes x | head -n 100000"})
    assert result.success is True
    assert result.output["truncated"] is True
    assert len(result.output["stdout"].encode()) <= 1000


async def test_a_model_requested_timeout_cannot_exceed_the_ceiling(tmp_path: Path):
    # The model may ask for a shorter run, never a longer one.
    result = await _enabled(tmp_path, timeout_s=0.5).run_command({"command": "sleep 30", "timeout_s": 30})
    assert result.success is True
    assert result.output["timed_out"] is True
