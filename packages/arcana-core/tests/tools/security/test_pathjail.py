"""Finding ``path_traversal`` — the filesystem jail refuses every escape.

Table-driven: a list of hostile paths, each expected to be refused by the real
``read_file`` / ``write_file`` handlers *before* any descriptor is opened. The
proof that nothing leaked is direct — a secret sentinel file lives outside the
jail, and the assertions show its bytes are never returned and a blocked write
never creates or mutates a file outside the allowed root.

Covers the filesystem path jail shared by every path-taking builtin (``read_file``,
``write_file``, ``move``, ``copy``, ``delete_*``, …), including symlink escape.
"""

from pathlib import Path

import pytest

from tests.support.tools import fs_tools

pytestmark = pytest.mark.security

#: Guards this module discharges — see ``security/catalog.py``.
COVERS = frozenset({"builtin:pathjail"})

#: Block reasons the path jail raises; a refusal must be one of these, never an
#: incidental "file not found" that would mean the guard let the path through.
_JAIL_REASONS = ("path outside allowed roots", "symlink escape", "no allowed roots configured", "invalid path")

SECRET = "TOP-SECRET-do-not-exfiltrate"


@pytest.fixture
def outside_secret(tmp_path: Path) -> Path:
    """A secret file that sits *outside* the jail root (``workspace``)."""
    secret = tmp_path / "outside_secret.txt"
    secret.write_text(SECRET)
    return secret


def _traversal_targets(workspace: Path, outside_secret: Path) -> list[str]:
    """Hostile ``path`` arguments, all pointing outside the jail."""
    link = workspace / "link_to_secret.txt"
    link.symlink_to(outside_secret)  # a symlink escape planted inside the jail
    return [
        "../outside_secret.txt",  # relative traversal
        "../../etc/passwd",  # deeper relative traversal
        str(outside_secret),  # absolute path outside the root
        "/etc/passwd",  # absolute system path
        "link_to_secret.txt",  # symlink whose target escapes the jail
    ]


async def test_read_file_never_escapes_the_jail(workspace: Path, outside_secret: Path):
    tools = fs_tools(workspace)
    for target in _traversal_targets(workspace, outside_secret):
        result = await tools.read_file({"path": target})
        assert result.success is False, f"jail let {target!r} through"
        assert any(reason in (result.error or "") for reason in _JAIL_REASONS), result.error
        # Nothing was read: the secret's bytes never reach the model.
        assert result.output is None
        assert SECRET not in (str(result.output) + (result.error or ""))


async def test_write_file_never_escapes_the_jail(workspace: Path, tmp_path: Path):
    tools = fs_tools(workspace)
    outside_target = tmp_path / "planted.txt"
    for target in ("../planted.txt", str(outside_target), "/tmp/arcana-escape-probe.txt"):
        result = await tools.write_file({"path": target, "content": "payload"})
        assert result.success is False, f"jail let a write to {target!r} through"
        assert any(reason in (result.error or "") for reason in _JAIL_REASONS), result.error
    # The side effect never happened: no file was created outside the jail.
    assert not outside_target.exists()
    assert not Path("/tmp/arcana-escape-probe.txt").exists()
