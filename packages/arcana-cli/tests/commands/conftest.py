"""Fixtures shared by the command tests."""

from pathlib import Path

import pytest

from tests.support.world import World, install_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """A temp ``~/.arcana`` every command group points at, with a dict for the OS keyring."""
    return install_world(tmp_path, monkeypatch)
