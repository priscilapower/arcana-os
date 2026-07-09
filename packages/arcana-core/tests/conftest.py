"""Root fixtures shared across the whole test suite."""

from collections.abc import Iterator
from pathlib import Path

import pytest

import arcana.observability as obs


@pytest.fixture
def audit_log(tmp_path: Path) -> Iterator[obs.AuditLog]:
    """Point the global audit log at a temp dir for the test, restoring it after."""
    previous = obs.get_audit_log()
    obs.configure_observability(tmp_path / "obs")
    log = obs.get_audit_log()
    assert log is not None
    yield log
    obs._audit_log = previous
