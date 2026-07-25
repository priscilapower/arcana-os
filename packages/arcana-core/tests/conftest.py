"""Root fixtures shared across the whole test suite."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import arcana.observability as obs


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An agent workspace to jail the filesystem tools to — never ``$HOME``.

    Defined once here because every filesystem test needs the same thing: a real
    directory that is *not* the temp root, so a test can put a file just outside
    the jail and assert the tools cannot reach it.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def audit_log(tmp_path: Path) -> Iterator[obs.AuditLog]:
    """Point the global audit log at a temp dir for the test, restoring it after."""
    previous = obs.get_audit_log()
    obs.configure_observability(tmp_path / "obs")
    log = obs.get_audit_log()
    assert log is not None
    yield log
    obs._audit_log = previous
