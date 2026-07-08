"""Tests for package-wide constants — the ``ARCANA_HOME`` env override.

``ARCANA_HOME`` is read once at import, so each case resolves it in a fresh
subprocess rather than reloading the module mid-suite.
"""

import os
import subprocess
import sys
from pathlib import Path


def _home_in_subprocess(env_value: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "ARCANA_HOME"}
    if env_value is not None:
        env["ARCANA_HOME"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", "from arcana_cli.constants import ARCANA_HOME; print(ARCANA_HOME)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_home_defaults_to_dot_arcana():
    assert _home_in_subprocess(None) == str(Path.home() / ".arcana")


def test_home_reads_arcana_home_env(tmp_path):
    target = tmp_path / "custom-home"
    assert _home_in_subprocess(str(target)) == str(target)
