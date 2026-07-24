"""Tests for the filesystem path jail. No LLM.

The priority suite for this slice: every one of these is a path that must not be
reachable. They mirror the adversarial-harness ``path_traversal`` class — a
canonical traversal, an absolute jump, a home-directory hop, a symlink out of the
root, and a symlink swapped in *after* the path was checked.
"""

import os
from pathlib import Path

import pytest

from arcana.tools.builtins.fs.pathguard import PathBlocked, PathGuard, canonical_path, is_within


def _guard(root: Path, **kwargs: object) -> PathGuard:
    return PathGuard([root], **kwargs)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# Containment — the traversal class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "escape",
    [
        "../../etc/passwd",
        "../outside.txt",
        "/etc/passwd",
        "~/.ssh/id_rsa",
        "~/.arcana/secrets/credentials.enc",
        "subdir/../../../etc/hosts",
    ],
)
def test_paths_outside_the_root_are_blocked(tmp_path: Path, escape: str):
    guard = _guard(tmp_path / "workspace")
    with pytest.raises(PathBlocked, match="outside allowed roots"):
        guard.resolve(escape)


def test_path_inside_the_root_resolves(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    assert _guard(root).resolve("notes.md") == root / "notes.md"


def test_relative_paths_anchor_to_the_workspace_not_the_process_cwd(tmp_path: Path, monkeypatch):
    # A model writing "notes.md" means its own workspace. Anchoring to the
    # process CWD would both surprise and reach outside the jail.
    root = tmp_path / "workspace"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert _guard(root).resolve("notes.md") == root / "notes.md"


def test_no_roots_blocks_every_path(tmp_path: Path):
    # Default-closed: an adapter built with no agent context reaches nothing.
    guard = PathGuard([])
    with pytest.raises(PathBlocked, match="no allowed roots"):
        guard.resolve(str(tmp_path / "anything.txt"))


def test_empty_and_non_string_paths_are_rejected(tmp_path: Path):
    guard = _guard(tmp_path)
    for bad in ("", "   ", None, 42, ["a"]):
        with pytest.raises(PathBlocked, match="missing 'path'"):
            guard.resolve(bad)


def test_sibling_directory_sharing_a_name_prefix_is_not_inside(tmp_path: Path):
    # A string prefix check would let "workspace-other" pass as "workspace".
    root = tmp_path / "workspace"
    root.mkdir()
    (tmp_path / "workspace-other").mkdir()
    with pytest.raises(PathBlocked, match="outside allowed roots"):
        _guard(root).resolve(str(tmp_path / "workspace-other" / "f.txt"))


def test_is_within_matches_the_root_itself_and_descendants(tmp_path: Path):
    assert is_within(tmp_path, tmp_path)
    assert is_within(tmp_path / "a" / "b", tmp_path)
    assert not is_within(tmp_path.parent, tmp_path)


# ---------------------------------------------------------------------------
# Symlinks — escape and TOCTOU
# ---------------------------------------------------------------------------


def test_symlink_pointing_out_of_the_root_is_blocked(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    (root / "link.txt").symlink_to(secret)

    with pytest.raises(PathBlocked, match="outside allowed roots"):
        _guard(root).resolve("link.txt")


def test_symlinked_parent_directory_pointing_out_is_blocked(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("x")
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathBlocked, match="outside allowed roots"):
        _guard(root).resolve("escape/f.txt")


def test_symlink_staying_inside_the_root_resolves_to_its_target(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("fine")
    (root / "alias.txt").symlink_to(real)

    assert _guard(root).resolve("alias.txt") == real


def test_symlink_swapped_after_resolve_is_refused_at_open(tmp_path: Path):
    """The TOCTOU case: the path checked out, then the file became a symlink."""
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "f.txt"
    target.write_text("safe")
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")

    guard = _guard(root)
    resolved = guard.resolve("f.txt")

    # The swap happens in the window between resolve() and the open.
    target.unlink()
    target.symlink_to(secret)

    with pytest.raises(PathBlocked, match="symlink escape"):
        guard.read_bytes(resolved, 1024)


def test_follow_symlinks_opt_in_reads_through_the_link(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("fine")
    link = root / "alias.txt"
    link.symlink_to(real)

    guard = _guard(root, follow_symlinks=True)
    data, truncated = guard.read_bytes(link, 1024)
    assert data == b"fine"
    assert not truncated


# ---------------------------------------------------------------------------
# File type and caps
# ---------------------------------------------------------------------------


def test_non_regular_files_are_refused(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    fifo = root / "pipe"
    os.mkfifo(fifo)

    # Must not block waiting for a writer, and must not read the FIFO.
    with pytest.raises(PathBlocked, match="not a regular file"):
        _guard(root).read_bytes(fifo, 1024)


def test_read_is_capped_and_flags_truncation(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "big.txt"
    target.write_text("x" * 100)

    guard = _guard(root)
    data, truncated = guard.read_bytes(target, 10)
    assert data == b"x" * 10
    assert truncated

    data, truncated = guard.read_bytes(target, 100)
    assert len(data) == 100
    assert not truncated


def test_resolve_for_write_refuses_an_existing_directory(tmp_path: Path):
    root = tmp_path / "workspace"
    (root / "sub").mkdir(parents=True)
    with pytest.raises(PathBlocked, match="is a directory"):
        _guard(root).resolve("sub", for_write=True)


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def test_list_entries_classifies_by_lstat_without_following(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("hello")
    (root / "sub").mkdir()
    (root / "link").symlink_to(tmp_path / "elsewhere.txt")
    os.mkfifo(root / "pipe")

    entries, truncated = _guard(root).list_entries(root, 100)

    assert [(e.name, e.kind) for e in entries] == [
        ("a.txt", "file"),
        ("link", "symlink"),
        ("pipe", "other"),
        ("sub", "dir"),
    ]
    assert not truncated
    assert next(e.size for e in entries if e.name == "a.txt") == 5


def test_list_entries_caps_and_flags_truncation(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(5):
        (root / f"f{index}").write_text("x")

    entries, truncated = _guard(root).list_entries(root, 2)

    assert [e.name for e in entries] == ["f0", "f1"]  # sorted, so the cap is deterministic
    assert truncated


def test_list_entries_refuses_a_regular_file(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("x")

    with pytest.raises(PathBlocked, match="not a directory"):
        _guard(root).list_entries(target, 100)


def test_list_entries_refuses_a_directory_symlinked_in_after_resolve(tmp_path: Path):
    """The TOCTOU case again, on the listing path.

    The refusal is what matters, not its wording: opening a symlink with
    ``O_DIRECTORY|O_NOFOLLOW`` reports ``ELOOP`` on Linux but ``ENOTDIR`` on
    macOS, so the message is platform-dependent while the block is not.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "sub"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")

    guard = _guard(root)
    resolved = guard.resolve("sub")

    target.rmdir()
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathBlocked) as excinfo:
        guard.list_entries(resolved, 100)
    assert excinfo.value.reason in ("symlink escape", "not a directory")


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_atomic_write_creates_parents_inside_the_jail(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    guard = _guard(root)
    target = guard.resolve("nested/deep/file.txt", for_write=True)

    guard.atomic_write(target, b"hello")

    assert target.read_bytes() == b"hello"
    assert is_within(target, root)


def test_atomic_write_leaves_no_temp_file_behind(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    guard = _guard(root)
    target = root / "f.txt"

    guard.atomic_write(target, b"one")
    guard.atomic_write(target, b"two")

    assert target.read_bytes() == b"two"
    assert [p.name for p in root.iterdir()] == ["f.txt"]


def test_atomic_write_through_a_symlink_replaces_the_link_not_its_target(tmp_path: Path):
    # os.replace acts on the name, so a link cannot redirect a write outside.
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    link = root / "link.txt"
    link.symlink_to(outside)

    _guard(root).atomic_write(link, b"new")

    assert outside.read_text() == "original"
    assert not link.is_symlink()
    assert link.read_bytes() == b"new"


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def test_root_for_returns_the_containing_root(tmp_path: Path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    guard = PathGuard([first, second])

    assert guard.root_for(second / "a" / "b.txt") == second


def test_root_for_rejects_a_path_under_no_root(tmp_path: Path):
    guard = PathGuard([tmp_path / "one"])
    with pytest.raises(PathBlocked, match="outside allowed roots"):
        guard.root_for(tmp_path / "two" / "f.txt")


def test_roots_are_canonicalized_so_a_symlinked_root_still_matches(tmp_path: Path):
    real = tmp_path / "real-workspace"
    real.mkdir()
    linked = tmp_path / "workspace"
    linked.symlink_to(real, target_is_directory=True)

    guard = PathGuard([linked])
    assert guard.roots == [canonical_path(real)]
    assert guard.resolve("f.txt") == real / "f.txt"
