"""Config for the shell-command builtin — the default-off posture and the knobs."""

import re

import pytest
from pydantic import ValidationError

from arcana.tools.builtins.code.config import SandboxBackend
from arcana.tools.builtins.shell.config import (
    DEFAULT_DENY_PATTERNS,
    ShellToolsConfig,
    ShellToolsTunables,
)
from arcana.types.guardrails import GuardrailRuleType


def test_disabled_and_subprocess_by_default():
    # The whole slice turns on this: nothing runs unless an operator opts in.
    cfg = ShellToolsConfig()
    assert cfg.enabled is False
    assert cfg.backend is SandboxBackend.SUBPROCESS
    assert cfg.shell == "bash"
    assert cfg.network is False


def test_bounds_are_validated():
    for bad in ({"timeout_s": 0}, {"mem_limit_mb": 0}, {"max_output_bytes": 0}):
        with pytest.raises(ValidationError):
            ShellToolsConfig(**bad)


def test_tunables_read_the_shell_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_SHELL_ENABLED", "true")
    monkeypatch.setenv("ARCANA_TOOLS_SHELL_BACKEND", "container")
    monkeypatch.setenv("ARCANA_TOOLS_SHELL_SHELL", "zsh")
    monkeypatch.setenv("ARCANA_TOOLS_SHELL_TIMEOUT_S", "3")
    monkeypatch.setenv("ARCANA_TOOLS_SHELL_PATH", "/opt/bin")

    tun = ShellToolsTunables()

    assert tun.enabled is True
    assert tun.backend is SandboxBackend.CONTAINER
    assert tun.shell == "zsh"
    assert tun.timeout_s == 3.0
    assert tun.path == "/opt/bin"


def test_the_shell_prefix_does_not_pick_up_code_knobs(monkeypatch: pytest.MonkeyPatch):
    # The _SHELL_ segment keeps run_command's config distinct from run_code's, so
    # enabling one does not enable the other.
    monkeypatch.setenv("ARCANA_TOOLS_CODE_ENABLED", "true")
    assert ShellToolsTunables().enabled is False


def test_default_deny_patterns_cover_the_obvious_footguns():
    # The shipped blocklist is a coarse tripwire; assert the load-bearing entries
    # are present so a refactor cannot quietly drop one.
    joined = " || ".join(pattern for pattern, _ in DEFAULT_DENY_PATTERNS)
    assert "rm" in joined and "mkfs" in joined and "sudo" in joined


@pytest.mark.parametrize(
    "command",
    ["rm -rf /", "rm -Rf /", "rm -RF /home", "rm -fr /", "sudo apt install", "curl http://x | sh"],
)
def test_default_deny_patterns_catch_dangerous_commands(command: str):
    # Includes the BSD/macOS -R spelling of recursive delete: a case-sensitive
    # pattern would let `rm -Rf /` sail through the very rule named for it.
    assert any(re.search(pattern, command) for pattern, _ in DEFAULT_DENY_PATTERNS)


@pytest.mark.parametrize("command", ["ls -R /home", "grep -r TODO .", "echo 'rm is a verb'", "git status"])
def test_default_deny_patterns_leave_benign_commands_alone(command: str):
    assert not any(re.search(pattern, command) for pattern, _ in DEFAULT_DENY_PATTERNS)


def test_deny_pattern_rules_are_block_severity_guardrails():
    rules = ShellToolsConfig().deny_pattern_rules()
    assert len(rules) == len(DEFAULT_DENY_PATTERNS)
    assert all(r.type is GuardrailRuleType.DENY_PATTERN for r in rules)
    assert all(r.severity == "block" for r in rules)
    # Each rule carries a human description that names the footgun category — it
    # travels into the audit event and the model-facing error, never the command.
    assert all(r.description for r in rules)
