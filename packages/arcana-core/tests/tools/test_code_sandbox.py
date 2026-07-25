"""The sandbox backends: the real subprocess jail, the argv builders, and degradation.

The subprocess tests spawn a real (local, hermetic) Python child — no network, no
external service — so they exercise the actual isolation the default backend
provides. The bubblewrap/container backends cannot run without their binaries, so
their *argv* (the security surface) is asserted as a pure function, and their
absence is asserted to degrade rather than crash.
"""

from pathlib import Path

import pytest

from arcana.tools.builtins.code.config import CodeLanguage, CodeToolsConfig, SandboxBackend
from arcana.tools.builtins.code.sandbox import (
    SandboxUnavailable,
    SubprocessSandbox,
    bubblewrap,
    container,
    make_sandbox,
)
from arcana.tools.builtins.code.sandbox.process import program_invocation


async def _run(
    code: str,
    *,
    workspace: Path,
    timeout_s: float = 5.0,
    mem_limit_mb: int = 512,
    max_output_bytes: int = 65536,
):
    return await SubprocessSandbox().run(
        code,
        language=CodeLanguage.PYTHON,
        timeout_s=timeout_s,
        mem_limit_mb=mem_limit_mb,
        workspace=workspace,
        env={"HOME": str(workspace), "PATH": "/usr/bin:/bin"},
        max_output_bytes=max_output_bytes,
    )


# ---------------------------------------------------------------------------
# SubprocessSandbox — real execution
# ---------------------------------------------------------------------------


async def test_runs_python_and_captures_stdout(tmp_path: Path):
    result = await _run("print(6 * 7)", workspace=tmp_path)
    assert result.exit_code == 0
    assert result.stdout.strip() == "42"
    assert result.timed_out is False
    assert result.truncated is False


async def test_nonzero_exit_is_carried_not_a_failure(tmp_path: Path):
    # A program that errors is a *successful* run carrying its exit code.
    result = await _run("import sys; sys.exit(7)", workspace=tmp_path)
    assert result.exit_code == 7
    assert result.timed_out is False


async def test_stderr_is_captured_separately(tmp_path: Path):
    result = await _run("import sys; sys.stderr.write('oops')", workspace=tmp_path)
    assert "oops" in result.stderr
    assert result.stdout == ""


async def test_the_child_environment_holds_no_host_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # NFR2: the sandbox child gets exactly the env it is handed, never the
    # parent's — a secret in Arcana's own environment must be unreachable.
    monkeypatch.setenv("ARCANA_SUPER_SECRET", "do-not-leak")
    result = await _run(
        "import os; print('ARCANA_SUPER_SECRET' in os.environ); print(os.environ.get('HOME'))",
        workspace=tmp_path,
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "False"  # the parent's secret did not leak in
    assert lines[1] == str(tmp_path)  # HOME points at the scratch workspace, not the real home


async def test_a_timeout_kills_the_run_and_flags_it(tmp_path: Path):
    # A hang is bounded by the wall clock and killed via its process group; the
    # call returns promptly rather than blocking the loop.
    result = await _run("import time; time.sleep(30)", workspace=tmp_path, timeout_s=0.5)
    assert result.timed_out is True


async def test_a_hang_after_closing_output_still_times_out(tmp_path: Path):
    # The escape a naive design misses: a child that closes stdout/stderr gives the
    # readers an early EOF, so the wall clock must bound the process reap too — not
    # just the stream drain — or this would run unbounded.
    import time as _time

    started = _time.monotonic()
    result = await _run(
        "import os, time; os.close(1); os.close(2); time.sleep(30)",
        workspace=tmp_path,
        timeout_s=0.5,
    )
    assert result.timed_out is True
    assert _time.monotonic() - started < 10  # bounded by the wall clock, not the 30s sleep


async def test_output_over_the_cap_is_truncated(tmp_path: Path):
    result = await _run("print('x' * 100_000)", workspace=tmp_path, max_output_bytes=1000)
    assert result.truncated is True
    assert len(result.stdout.encode()) <= 1000


async def test_the_workspace_is_the_working_directory(tmp_path: Path):
    result = await _run("import os; print(os.getcwd())", workspace=tmp_path)
    assert result.stdout.strip() == str(tmp_path)


# ---------------------------------------------------------------------------
# Backend selection + degradation
# ---------------------------------------------------------------------------


def test_make_sandbox_defaults_to_subprocess():
    assert isinstance(make_sandbox(CodeToolsConfig(enabled=True)), SubprocessSandbox)


def test_bubblewrap_without_the_binary_degrades(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.bubblewrap.shutil.which", lambda _: None)
    with pytest.raises(SandboxUnavailable, match="bwrap"):
        make_sandbox(CodeToolsConfig(enabled=True, backend=SandboxBackend.BUBBLEWRAP))


def test_container_without_the_runtime_degrades(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.container.shutil.which", lambda _: None)
    with pytest.raises(SandboxUnavailable, match="podman"):
        make_sandbox(CodeToolsConfig(enabled=True, backend=SandboxBackend.CONTAINER, container_command="podman"))


# ---------------------------------------------------------------------------
# program_invocation — argv + stdin per language
# ---------------------------------------------------------------------------


def test_program_invocation_feeds_python_and_bash_on_stdin():
    # A program source rides on stdin (kept out of the process table), so the argv
    # is code-independent and the stdin bytes carry it.
    py_argv, py_stdin = program_invocation(CodeLanguage.PYTHON, "print(1)", python="/usr/bin/python3")
    assert py_argv == ["/usr/bin/python3", "-I", "-S", "-"]
    assert py_stdin == b"print(1)"

    bash_argv, bash_stdin = program_invocation(CodeLanguage.BASH, "echo hi")
    assert bash_argv == ["bash", "--noprofile", "--norc", "-s"]
    assert bash_stdin == b"echo hi"


def test_program_invocation_runs_a_shell_command_via_dash_c():
    # The shell exception: a command string rides in argv via -c (empty stdin).
    # Bash gets its --noprofile --norc no-startup-file hardening.
    argv, stdin = program_invocation(CodeLanguage.SHELL, "git status | head", shell="bash")
    assert argv == ["bash", "--noprofile", "--norc", "-c", "git status | head"]
    assert stdin == b""
    # An absolute bash path is still recognised as bash.
    abs_argv, _ = program_invocation(CodeLanguage.SHELL, "ls", shell="/usr/bin/bash")
    assert abs_argv == ["/usr/bin/bash", "--noprofile", "--norc", "-c", "ls"]


def test_program_invocation_omits_bash_only_flags_for_a_non_bash_shell():
    # --noprofile/--norc are bash-specific; another shell would reject them, so it
    # runs with a plain -c (already non-interactive, so it reads no rc files).
    for shell in ("zsh", "sh", "dash"):
        argv, stdin = program_invocation(CodeLanguage.SHELL, "ls", shell=shell)
        assert argv == [shell, "-c", "ls"]
        assert "--noprofile" not in argv and "--norc" not in argv
        assert stdin == b""


# ---------------------------------------------------------------------------
# argv builders — the isolation surface, asserted without the binaries
# ---------------------------------------------------------------------------


def test_bubblewrap_argv_unshares_net_and_never_binds_home(tmp_path: Path):
    argv = bubblewrap.build_argv("bwrap", ["python", "-"], workspace=tmp_path, network=False)
    assert "--unshare-net" in argv
    # The scratch workspace is the only writable bind; $HOME / ~/.arcana are not.
    assert "--bind" in argv and str(tmp_path) in argv
    joined = " ".join(argv)
    assert str(Path.home()) not in joined
    assert ".arcana" not in joined


def test_bubblewrap_argv_keeps_the_net_namespace_when_allowed(tmp_path: Path):
    argv = bubblewrap.build_argv("bwrap", ["python", "-"], workspace=tmp_path, network=True)
    assert "--unshare-net" not in argv


def test_container_argv_isolates_network_and_filesystem(tmp_path: Path):
    argv = container.build_argv(
        "docker", "python:3-slim", ["python", "-"], name="run-1", workspace=tmp_path, mem_limit_mb=256, network=False
    )
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in argv
    assert "--memory" in argv and "256m" in argv
    # A stable --name so the timeout kill can target this run by identity.
    assert "--name" in argv and "run-1" in argv
    # --network none and the workspace mounted at the fixed in-container path.
    assert "none" in argv
    assert f"{tmp_path}:/workspace:rw" in argv


def test_container_argv_allows_the_network_when_asked(tmp_path: Path):
    argv = container.build_argv(
        "docker", "img", ["python", "-"], name="run-2", workspace=tmp_path, mem_limit_mb=256, network=True
    )
    assert "none" not in argv


def test_container_timeout_kills_the_container_by_name():
    # Killing the CLI client does not stop the container; the timeout teardown
    # must issue an explicit kill against the run's name.
    assert container.kill_command("podman", "arcana-run-code-abc") == ["podman", "kill", "arcana-run-code-abc"]
