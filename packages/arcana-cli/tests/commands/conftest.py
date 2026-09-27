"""Fixtures shared by the command tests."""

from pathlib import Path

import pytest
import rich.console

from tests.support.world import World, install_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A temp ``~/.arcana`` every command group points at, with a dict for the OS keyring."""
    return install_world(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def modern_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render as a modern terminal does on every OS, so the goldens are the same everywhere.

    On Windows, Rich treats a stream that isn't a VT console (the test runner's) as a
    legacy console: ASCII-safe box corners and one column narrower.
    """
    monkeypatch.setattr(rich.console, "detect_legacy_windows", lambda: False)
