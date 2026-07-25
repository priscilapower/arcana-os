"""Tests for the guarded tree walk. No LLM.

The handler tests cover what the directory tools *do*; these cover the walk they
all sit on, directly — traversal order, the no-follow rule, and each aggregate
cap — so a regression in the primitive is caught where it happens rather than
three tools downstream.
"""

import os
from pathlib import Path

import pytest

from arcana.tools.builtins.fs.pathguard import PathBlocked, PathGuard
from arcana.tools.builtins.fs.tree import GuardedTree, TreeCaps
from arcana.types.tool import FsEntryKind

GENEROUS = TreeCaps(max_tree_bytes=1 << 20, max_file_count=1000, max_depth=32)


def _tree(workspace: Path, caps: TreeCaps = GENEROUS) -> GuardedTree:
    return GuardedTree(PathGuard([workspace]), caps)


def _build(root: Path, *relatives: str) -> None:
    for relative in relatives:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative)


def test_walk_yields_parents_before_children(workspace: Path):
    # Copy creates directories before filling them, so pre-order is not an
    # incidental detail of the traversal — it is the contract.
    _build(workspace, "a/b/c.txt")

    seen = [(str(e.path.relative_to(workspace)), e.kind, e.depth) for e in _tree(workspace).walk(workspace)]

    assert seen == [
        ("a", FsEntryKind.DIR, 1),
        ("a/b", FsEntryKind.DIR, 2),
        ("a/b/c.txt", FsEntryKind.FILE, 3),
    ]


def test_walk_classifies_a_symlink_as_a_link_and_does_not_descend(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    seen = list(_tree(workspace).walk(workspace))

    assert [(e.path.name, e.kind) for e in seen] == [("escape", FsEntryKind.SYMLINK)]
    # The target's contents never appear: the link was never entered.
    assert not any("secret.txt" in str(e.path) for e in seen)


def test_walk_classifies_a_non_regular_entry_as_other(workspace: Path):
    os.mkfifo(workspace / "pipe")

    assert [e.kind for e in _tree(workspace).walk(workspace)] == [FsEntryKind.OTHER]


def test_walk_refuses_a_tree_past_the_depth_cap(workspace: Path):
    _build(workspace, "a/b/c/d.txt")
    caps = TreeCaps(max_tree_bytes=1 << 20, max_file_count=1000, max_depth=2)

    with pytest.raises(PathBlocked, match="deeper than the 2-level limit"):
        list(_tree(workspace, caps).walk(workspace))


def test_walk_refuses_a_tree_past_the_entry_cap(workspace: Path):
    _build(workspace, "a.txt", "b.txt", "c.txt")
    caps = TreeCaps(max_tree_bytes=1 << 20, max_file_count=2, max_depth=32)

    with pytest.raises(PathBlocked, match="more than the 2-entry limit"):
        list(_tree(workspace, caps).walk(workspace))


def test_the_entry_budget_is_shared_across_the_whole_recursion(workspace: Path):
    # A per-directory budget would let a wide, shallow tree multiply past the cap.
    _build(workspace, "one/a.txt", "two/b.txt", "three/c.txt")
    caps = TreeCaps(max_tree_bytes=1 << 20, max_file_count=4, max_depth=32)

    with pytest.raises(PathBlocked, match="4-entry limit"):
        list(_tree(workspace, caps).walk(workspace))


def test_an_unbounded_walk_ignores_the_caps_but_not_containment(workspace: Path, tmp_path: Path):
    # What a rollback runs on: a tree the caps would refuse still has to be
    # removable, or a cap failure would leave its own debris behind for good.
    _build(workspace, "a/b/c/d.txt")
    (workspace / "a" / "escape").symlink_to(tmp_path / "outside")
    caps = TreeCaps(max_tree_bytes=1, max_file_count=1, max_depth=1)

    stats = _tree(workspace, caps).remove(workspace / "a", bounded=False)

    assert not (workspace / "a").exists()
    assert stats.entries > 0


def test_remove_unlinks_a_symlink_without_touching_its_target(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    (workspace / "doomed").mkdir()
    (workspace / "doomed" / "escape").symlink_to(outside, target_is_directory=True)

    _tree(workspace).remove(workspace / "doomed")

    assert not (workspace / "doomed").exists()
    assert (outside / "secret.txt").read_text() == "classified"


def test_measure_counts_without_mutating(workspace: Path):
    _build(workspace, "a.txt", "deep/b.txt")

    stats = _tree(workspace).measure(workspace)

    assert (stats.files, stats.dirs, stats.entries) == (2, 1, 3)
    assert (workspace / "a.txt").exists()
    assert (workspace / "deep" / "b.txt").exists()
