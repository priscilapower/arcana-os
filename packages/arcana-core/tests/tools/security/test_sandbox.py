"""Finding ``code_exec`` — code execution is off by default and scrubs its env.

The dangerous builtins (``run_code``, ``run_command``) are *offered* to the model
so their schema is stable, but they refuse to run until an operator turns them on:
the default posture spawns nothing. That default-off gate is a fast, fully-mocked
check. The environment-scrub property — that no host secret can leak into executed
code — is asserted against a *real* subprocess (mocking the sandbox would let a
real leak through), so it carries ``@pytest.mark.slow`` and is gated out of the
default tier.

Covers the two execution-tool gates and the sandbox env scrub.
"""

import os
from pathlib import Path

import pytest

from arcana.tools.builtins.code.config import CodeLanguage, CodeToolsConfig
from arcana.tools.builtins.code.handlers import CodeTools, _scrubbed_env
from arcana.tools.builtins.code.sandbox import SubprocessSandbox, make_sandbox
from arcana.tools.builtins.shell.config import ShellToolsConfig
from arcana.tools.builtins.shell.handlers import ShellTools

pytestmark = pytest.mark.security

#: Guards this module discharges — see ``security/catalog.py``.
COVERS = frozenset({"builtin:run_code_disabled", "builtin:run_command_disabled"})


async def test_run_code_is_disabled_by_default():
    """With no configuration, ``run_code`` refuses and spawns nothing."""
    tools = CodeTools(CodeToolsConfig())  # default: enabled=False
    result = await tools.run_code({"code": "print('should never run')"})
    assert result.success is False
    assert result.error == "run_code is disabled"


async def test_run_command_is_disabled_by_default():
    """With no configuration, ``run_command`` refuses and spawns nothing."""
    tools = ShellTools(ShellToolsConfig())  # default: enabled=False
    result = await tools.run_command({"command": "echo should-never-run"})
    assert result.success is False
    assert "disabled" in (result.error or "")


def test_scrubbed_env_carries_no_host_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The child environment is a fixed allowlist, never a copy of the parent's."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-super-secret")

    env = _scrubbed_env(tmp_path)

    assert "ANTHROPIC_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "sk-super-secret" not in env.values()
    # HOME/TMPDIR point at the scratch workspace, not the real home.
    assert env["HOME"] == str(tmp_path)
    assert env["TMPDIR"] == str(tmp_path)


@pytest.mark.slow
async def test_executed_code_cannot_read_a_host_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real child process, enabled, cannot observe a secret from Arcana's env.

    Marked ``slow``: it spawns an actual Python subprocess. Mocking the sandbox
    here would defeat the point — only a real process proves the scrub holds.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret")
    tools = CodeTools(CodeToolsConfig(enabled=True))
    result = await tools.run_code({"code": "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'ABSENT'))"})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["stdout"].strip() == "ABSENT"
    assert "sk-super-secret" not in result.output["stdout"]


@pytest.mark.slow
async def test_executed_code_runs_in_a_throwaway_workspace_not_the_home(monkeypatch: pytest.MonkeyPatch):
    """Enabled ``run_code`` runs under a scratch HOME, never the user's real home."""
    real_home = os.path.expanduser("~")
    tools = CodeTools(CodeToolsConfig(enabled=True))
    result = await tools.run_code(
        {"code": "import os; print(os.path.expanduser('~'))", "language": CodeLanguage.PYTHON}
    )

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["stdout"].strip() != real_home


def test_subprocess_sandbox_is_the_default_backend():
    """A sanity anchor for the slow tier: the default backend is the subprocess jail."""
    assert isinstance(make_sandbox(CodeToolsConfig(enabled=True)), SubprocessSandbox)
