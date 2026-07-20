"""Tests for tool-execution tunables (ARCANA_TOOLS_* env overrides)."""

import pytest
from pydantic import ValidationError

from arcana.tools.config import ToolTunables


def test_tunables_defaults():
    tunables = ToolTunables()
    assert tunables.timeout_s == 30.0
    assert tunables.max_iterations == 5


def test_tunables_read_env_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_TIMEOUT_S", "12.5")
    monkeypatch.setenv("ARCANA_TOOLS_MAX_ITERATIONS", "9")
    tunables = ToolTunables()
    assert tunables.timeout_s == 12.5
    assert tunables.max_iterations == 9


def test_tunables_reject_non_positive_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_TIMEOUT_S", "0")
    with pytest.raises(ValidationError):
        ToolTunables()


def test_tunables_reject_zero_iterations(monkeypatch: pytest.MonkeyPatch):
    # At least one completion must always run — a cap below 1 is rejected at load.
    monkeypatch.setenv("ARCANA_TOOLS_MAX_ITERATIONS", "0")
    with pytest.raises(ValidationError):
        ToolTunables()


def test_tunables_ignore_unrelated_prefixed_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_SOMETHING_ELSE", "whatever")
    assert ToolTunables().timeout_s == 30.0
