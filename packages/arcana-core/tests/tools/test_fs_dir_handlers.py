"""Tests for the directory filesystem builtins. No LLM.

Every jail is a ``tmp_path``, never ``$HOME``. Three things separate these from
the single-file handlers, and each gets its own section below: a two-path call is
only as confined as its weaker argument, a recursive walk must never follow a
symlink out of the jail, and a tree operation has to be bounded in aggregate.

The load-bearing assertion throughout is what the *filesystem* looks like after a
refusal: a denied call must leave it exactly as it was, and a failed tree op must
leave no half-built destination behind.
"""

import errno
import os
from pathlib import Path
from typing import Any

import pytest

from arcana.tools.builtins.fs import handlers as handlers_module
from arcana.tools.builtins.fs.config import TRASH_DIR_NAME
from arcana.tools.builtins.fs.handlers import FsTools
from arcana.types.tool import ToolResult
from tests.support.tools import fs_tools


def _output(result: ToolResult) -> dict[str, Any]:
    """The success payload, narrowed — every handler returns a JSON object."""
    assert isinstance(result.output, dict)
    return result.output


def _tree(root: Path, *, files: dict[str, str]) -> Path:
    """Build a fixture tree under ``root`` from ``relative path -> contents``."""
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return root


def _relative_contents(root: Path) -> dict[str, str]:
    """Every regular file under ``root``, keyed by its path relative to it."""
    return {
        str(path.relative_to(root)): path.read_text()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _partials(directory: Path) -> list[Path]:
    """Staging directories a rolled-back tree op would have left behind."""
    return [path for path in directory.iterdir() if path.name.endswith(".partial")]


# ---------------------------------------------------------------------------
# make_dir
# ---------------------------------------------------------------------------


async def test_make_dir_creates_a_directory(workspace: Path):
    result = await fs_tools(workspace).make_dir({"path": "notes"})

    assert result.success
    assert result.output == {"path": str(workspace / "notes"), "created": True}
    assert (workspace / "notes").is_dir()


async def test_make_dir_without_parents_refuses_a_missing_parent(workspace: Path):
    result = await fs_tools(workspace).make_dir({"path": "a/b"})

    assert not result.success
    assert result.error == "file not found"
    assert not (workspace / "a").exists()


async def test_make_dir_with_parents_creates_intermediates(workspace: Path):
    result = await fs_tools(workspace).make_dir({"path": "a/b/c", "parents": True})

    assert result.success
    assert (workspace / "a" / "b" / "c").is_dir()


async def test_make_dir_refuses_an_existing_directory(workspace: Path):
    (workspace / "notes").mkdir()

    result = await fs_tools(workspace).make_dir({"path": "notes"})

    assert not result.success
    assert result.error == "blocked: path already exists"


async def test_make_dir_exist_ok_succeeds_without_creating(workspace: Path):
    (workspace / "notes").mkdir()

    result = await fs_tools(workspace).make_dir({"path": "notes", "exist_ok": True})

    assert result.success
    assert _output(result)["created"] is False


async def test_make_dir_exist_ok_still_refuses_an_existing_file(workspace: Path):
    # exist_ok says "the directory may already be there", not "anything may be".
    (workspace / "notes").write_text("a file, not a directory")

    result = await fs_tools(workspace).make_dir({"path": "notes", "exist_ok": True})

    assert not result.success
    assert result.error == "blocked: path exists and is not a directory"
    assert (workspace / "notes").read_text() == "a file, not a directory"


async def test_make_dir_parents_is_bounded_by_the_depth_cap(workspace: Path):
    # A pathological path must not be able to mint an unbounded number of dirs.
    deep = "/".join("abcdefgh")

    result = await fs_tools(workspace, max_depth=3).make_dir({"path": deep, "parents": True})

    assert not result.success
    assert result.error is not None
    assert "over the 3-level limit" in result.error
    assert not (workspace / "a").exists()


@pytest.mark.parametrize("tool", ["move", "copy"])
async def test_a_deep_destination_is_bounded_by_the_same_depth_cap(workspace: Path, tool: str):
    # The amplification make_dir is capped against, reached the other way round:
    # the levels are implied by the destination rather than asked for directly.
    (workspace / "a.txt").write_text("body")
    deep = "/".join("abcdefgh") + "/out.txt"

    result = await getattr(fs_tools(workspace, max_depth=3), tool)({"src": "a.txt", "dst": deep})

    assert not result.success
    assert result.error is not None
    assert "over the 3-level limit" in result.error
    assert not (workspace / "a").exists()
    assert (workspace / "a.txt").read_text() == "body"


async def test_make_dir_outside_the_jail_is_blocked(workspace: Path, tmp_path: Path):
    result = await fs_tools(workspace).make_dir({"path": "../escape"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"
    assert not (tmp_path / "escape").exists()


# ---------------------------------------------------------------------------
# Two-path safety — move and copy resolve both arguments before acting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["move", "copy"])
@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ({"src": "../secret.txt", "dst": "stolen.txt"}, "path outside allowed roots"),
        ({"src": "keep.txt", "dst": "../exfiltrated.txt"}, "path outside allowed roots"),
        ({"src": "keep.txt", "dst": "keep.txt"}, "destination is inside the source"),
        ({"dst": "somewhere.txt"}, "missing 'path'"),
        ({"src": "keep.txt"}, "missing 'path'"),
    ],
)
async def test_a_bad_path_in_either_argument_denies_the_whole_call(
    workspace: Path,
    tmp_path: Path,
    tool: str,
    args: dict[str, Any],
    reason: str,
):
    # A two-path op is only as confined as its weaker argument: an out-of-jail
    # src is an exfiltration source and an out-of-jail dst is an exfiltration
    # destination, so either one denies the call outright.
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    (workspace / "keep.txt").write_text("mine")

    result = await getattr(fs_tools(workspace), tool)(args)

    assert not result.success
    assert result.error == f"blocked: {reason}"
    assert secret.read_text() == "classified"
    assert not (tmp_path / "exfiltrated.txt").exists()
    assert not (tmp_path / "stolen.txt").exists()
    assert (workspace / "keep.txt").read_text() == "mine"


@pytest.mark.parametrize("tool", ["move", "copy"])
async def test_a_destination_inside_the_source_is_refused(workspace: Path, tool: str):
    # A copy that recursed into its own growing output would never terminate.
    _tree(workspace, files={"tree/a.txt": "a"})

    result = await getattr(fs_tools(workspace), tool)({"src": "tree", "dst": "tree/inner"})

    assert not result.success
    assert result.error == "blocked: destination is inside the source"
    assert not (workspace / "tree" / "inner").exists()


@pytest.mark.parametrize("tool", ["move", "copy"])
async def test_a_symlinked_argument_pointing_out_of_the_jail_is_refused(workspace: Path, tmp_path: Path, tool: str):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    result = await getattr(fs_tools(workspace), tool)({"src": "escape", "dst": "copied"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"
    assert not (workspace / "copied").exists()
    assert (outside / "secret.txt").read_text() == "classified"


@pytest.mark.parametrize("tool", ["move", "copy"])
async def test_a_missing_source_is_refused(workspace: Path, tool: str):
    result = await getattr(fs_tools(workspace), tool)({"src": "nope", "dst": "somewhere"})

    assert not result.success
    assert result.error == "blocked: source not found"
    assert not (workspace / "somewhere").exists()


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


async def test_move_renames_a_file(workspace: Path):
    (workspace / "old.txt").write_text("body")

    result = await fs_tools(workspace).move({"src": "old.txt", "dst": "new.txt"})

    assert result.success
    assert _output(result) == {
        "src": str(workspace / "old.txt"),
        "dst": str(workspace / "new.txt"),
        "moved": True,
        "atomic": True,
    }
    assert not (workspace / "old.txt").exists()
    assert (workspace / "new.txt").read_text() == "body"


async def test_move_relocates_a_whole_tree(workspace: Path):
    _tree(workspace, files={"src/a.txt": "a", "src/deep/b.txt": "b"})

    result = await fs_tools(workspace).move({"src": "src", "dst": "archive/moved"})

    assert result.success
    assert not (workspace / "src").exists()
    assert _relative_contents(workspace / "archive" / "moved") == {"a.txt": "a", "deep/b.txt": "b"}


async def test_move_refuses_an_existing_destination(workspace: Path):
    (workspace / "a.txt").write_text("source")
    (workspace / "b.txt").write_text("destination")

    result = await fs_tools(workspace).move({"src": "a.txt", "dst": "b.txt"})

    assert not result.success
    assert result.error is not None
    assert "destination already exists" in result.error
    assert (workspace / "a.txt").read_text() == "source"
    assert (workspace / "b.txt").read_text() == "destination"


async def test_move_overwrite_replaces_the_destination(workspace: Path):
    (workspace / "a.txt").write_text("source")
    (workspace / "b.txt").write_text("destination")

    result = await fs_tools(workspace).move({"src": "a.txt", "dst": "b.txt", "overwrite": True})

    assert result.success
    assert (workspace / "b.txt").read_text() == "source"
    assert not (workspace / "a.txt").exists()


async def test_move_overwrite_replaces_a_whole_destination_tree(workspace: Path):
    _tree(workspace, files={"src/a.txt": "new", "dst/stale.txt": "old"})

    result = await fs_tools(workspace).move({"src": "src", "dst": "dst", "overwrite": True})

    assert result.success
    assert _relative_contents(workspace / "dst") == {"a.txt": "new"}


def _rename_across_devices(monkeypatch: pytest.MonkeyPatch, only: Path) -> None:
    """Make ``os.rename`` of exactly ``only`` fail as a cross-device link would.

    Scoped to one path so the staging commit — a same-directory rename that a
    real ``EXDEV`` would never affect — still goes through.
    """
    real_rename = os.rename

    def fake_rename(src: Any, dst: Any, **kwargs: Any) -> None:
        if Path(src) == only:
            raise OSError(errno.EXDEV, "cross-device link")
        real_rename(src, dst, **kwargs)

    monkeypatch.setattr(handlers_module.os, "rename", fake_rename)


async def test_move_across_filesystems_falls_back_to_a_guarded_copy(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    _tree(workspace, files={"src/a.txt": "a", "src/deep/b.txt": "b"})
    _rename_across_devices(monkeypatch, workspace / "src")

    result = await fs_tools(workspace).move({"src": "src", "dst": "dst"})

    assert result.success
    # Reported honestly: there is no atomic rename across a device boundary, and
    # a caller that needs one has to know it did not get one.
    assert _output(result)["atomic"] is False
    assert not (workspace / "src").exists()
    assert _relative_contents(workspace / "dst") == {"a.txt": "a", "deep/b.txt": "b"}
    assert _partials(workspace) == []


async def test_a_failed_cross_filesystem_move_leaves_the_source_intact(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    # The failure that must never lose data: the copy half fails, so the source
    # is still the only copy and no partial destination is left claiming to be one.
    _tree(workspace, files={"src/a.txt": "a", "src/big.txt": "x" * 100})
    _rename_across_devices(monkeypatch, workspace / "src")

    result = await fs_tools(workspace, max_write_bytes=10).move({"src": "src", "dst": "dst"})

    assert not result.success
    assert _relative_contents(workspace / "src") == {"a.txt": "a", "big.txt": "x" * 100}
    assert not (workspace / "dst").exists()
    assert _partials(workspace) == []


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


async def test_copy_duplicates_a_file(workspace: Path):
    (workspace / "a.txt").write_text("body")

    result = await fs_tools(workspace).copy({"src": "a.txt", "dst": "b.txt"})

    assert result.success
    assert _output(result)["files_copied"] == 1
    assert _output(result)["bytes_copied"] == 4
    assert (workspace / "a.txt").read_text() == "body"
    assert (workspace / "b.txt").read_text() == "body"


async def test_copy_duplicates_a_whole_tree(workspace: Path):
    _tree(workspace, files={"src/a.txt": "a", "src/deep/b.txt": "bb", "src/deep/deeper/c.txt": "ccc"})

    result = await fs_tools(workspace).copy({"src": "src", "dst": "dst"})

    assert result.success
    assert _output(result)["files_copied"] == 3
    assert _output(result)["bytes_copied"] == 6
    assert _relative_contents(workspace / "dst") == _relative_contents(workspace / "src")
    assert _partials(workspace) == []


async def test_copy_refuses_an_existing_destination(workspace: Path):
    (workspace / "a.txt").write_text("source")
    (workspace / "b.txt").write_text("destination")

    result = await fs_tools(workspace).copy({"src": "a.txt", "dst": "b.txt"})

    assert not result.success
    assert (workspace / "b.txt").read_text() == "destination"


async def test_copy_overwrite_replaces_the_destination_tree(workspace: Path):
    _tree(workspace, files={"src/a.txt": "new", "dst/stale.txt": "old"})

    result = await fs_tools(workspace).copy({"src": "src", "dst": "dst", "overwrite": True})

    assert result.success
    assert _relative_contents(workspace / "dst") == {"a.txt": "new"}
    assert _relative_contents(workspace / "src") == {"a.txt": "new"}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"max_tree_bytes": 4}, "larger than the 4-byte limit"),
        ({"max_file_count": 2}, "more than the 2-entry limit"),
        ({"max_depth": 2}, "deeper than the 2-level limit"),
        ({"max_write_bytes": 2}, "per-file cap"),
    ],
    ids=["tree_bytes", "file_count", "depth", "per_file_bytes"],
)
async def test_every_cap_aborts_the_copy_and_rolls_back(workspace: Path, overrides: dict[str, int], expected: str):
    _tree(workspace, files={"src/a.txt": "aaa", "src/deep/b.txt": "bbb", "src/deep/deeper/c.txt": "ccc"})
    before = _relative_contents(workspace / "src")

    result = await fs_tools(workspace, **overrides).copy({"src": "src", "dst": "dst"})

    assert not result.success
    assert result.error is not None
    assert expected in result.error
    # Rolled back completely: no destination, and no staging tree pretending to be one.
    assert not (workspace / "dst").exists()
    assert _partials(workspace) == []
    assert _relative_contents(workspace / "src") == before


async def test_a_failed_overwrite_copy_leaves_the_old_destination_alone(workspace: Path):
    # The destination is only destroyed once a whole replacement exists, so a
    # copy that trips a cap cannot take the previous contents down with it.
    _tree(workspace, files={"src/big.txt": "x" * 100, "dst/precious.txt": "keep me"})

    result = await fs_tools(workspace, max_write_bytes=10).copy({"src": "src", "dst": "dst", "overwrite": True})

    assert not result.success
    assert _relative_contents(workspace / "dst") == {"precious.txt": "keep me"}
    assert _partials(workspace) == []


# ---------------------------------------------------------------------------
# Symlink-safe recursion — the copytree / rmtree footgun, closed
# ---------------------------------------------------------------------------


async def test_copy_skips_a_symlink_pointing_out_of_the_jail(workspace: Path, tmp_path: Path):
    # Recreating this link would hand the destination tree a door out of the
    # jail; following it would make copy an exfiltration primitive outright.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    _tree(workspace, files={"src/a.txt": "a"})
    (workspace / "src" / "escape").symlink_to(outside, target_is_directory=True)

    result = await fs_tools(workspace).copy({"src": "src", "dst": "dst"})

    assert result.success
    assert _output(result)["skipped"] == 1
    assert not (workspace / "dst" / "escape").is_symlink()
    assert not (workspace / "dst" / "escape").exists()
    # Never descended: the target's contents were not copied anywhere.
    assert _relative_contents(workspace / "dst") == {"a.txt": "a"}
    assert (outside / "secret.txt").read_text() == "classified"


async def test_copy_recreates_a_symlink_that_stays_inside_the_jail(workspace: Path):
    _tree(workspace, files={"src/a.txt": "a", "target.txt": "pointed at"})
    (workspace / "src" / "link").symlink_to(workspace / "target.txt")

    result = await fs_tools(workspace).copy({"src": "src", "dst": "dst"})

    assert result.success
    assert _output(result)["skipped"] == 0
    # Copied as a link, not as a dereferenced copy of its target.
    assert (workspace / "dst" / "link").is_symlink()


async def test_copy_skips_a_non_regular_entry(workspace: Path):
    _tree(workspace, files={"src/a.txt": "a"})
    os.mkfifo(workspace / "src" / "pipe")

    result = await fs_tools(workspace).copy({"src": "src", "dst": "dst"})

    assert result.success
    assert _output(result)["skipped"] == 1
    assert _relative_contents(workspace / "dst") == {"a.txt": "a"}


async def test_delete_dir_unlinks_an_escaping_symlink_without_following_it(workspace: Path, tmp_path: Path):
    # The rmtree footgun: a naive recursive delete follows this link and empties
    # a directory the agent was never allowed to reach.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    _tree(workspace, files={"doomed/a.txt": "a"})
    (workspace / "doomed" / "escape").symlink_to(outside, target_is_directory=True)

    result = await fs_tools(workspace, hard_delete=True).delete_dir({"path": "doomed"})

    assert result.success
    assert not (workspace / "doomed").exists()
    assert (outside / "secret.txt").read_text() == "classified"


async def test_a_node_that_canonicalizes_out_of_the_jail_stops_the_walk(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Per-node containment, not just per-argument — the mid-walk TOCTOU case.

    A link swapped in *after* the scan classified it as a plain directory would
    still be entered on the strength of that stale classification. Standing in
    for the race, the canonical path of one node is made to resolve outside the
    jail: the walk has to notice at that node rather than at the argument.
    """
    _tree(workspace, files={"src/a.txt": "a", "src/swapped/b.txt": "b"})
    from arcana.tools.builtins.fs import tree as tree_module

    real_canonical = tree_module.canonical_path
    swapped = workspace / "src" / "swapped"

    def fake_canonical(raw: Any) -> Path:
        return tmp_path / "elsewhere" if Path(raw) == swapped else real_canonical(raw)

    monkeypatch.setattr(tree_module, "canonical_path", fake_canonical)

    result = await fs_tools(workspace).copy({"src": "src", "dst": "dst"})

    assert not result.success
    assert result.error == "blocked: symlink escape mid-walk"
    assert not (workspace / "dst").exists()
    assert _partials(workspace) == []


# ---------------------------------------------------------------------------
# delete_dir
# ---------------------------------------------------------------------------


async def test_delete_dir_soft_deletes_the_whole_tree(workspace: Path):
    _tree(workspace, files={"doomed/a.txt": "a", "doomed/deep/b.txt": "b"})

    result = await fs_tools(workspace).delete_dir({"path": "doomed"})

    assert result.success
    output = _output(result)
    assert output["outcome"] == "trashed"
    assert output["entry_count"] == 3  # a.txt, deep/, deep/b.txt
    assert not (workspace / "doomed").exists()

    # Recoverable: the whole subtree is still there, under the trash.
    trashed = list((workspace / TRASH_DIR_NAME).iterdir())
    assert len(trashed) == 1
    assert _relative_contents(trashed[0]) == {"a.txt": "a", "deep/b.txt": "b"}


async def test_delete_dir_hard_delete_unlinks_the_tree(workspace: Path):
    _tree(workspace, files={"doomed/a.txt": "a", "doomed/deep/b.txt": "b"})

    result = await fs_tools(workspace, hard_delete=True).delete_dir({"path": "doomed"})

    assert result.success
    assert _output(result)["outcome"] == "deleted"
    assert not (workspace / "doomed").exists()
    assert not (workspace / TRASH_DIR_NAME).exists()


async def test_delete_dir_refuses_the_jail_root(workspace: Path):
    # A workspace's contents can be emptied; the workspace itself cannot be
    # removed out from under the jail — that would take the trash with it.
    (workspace / "keep.txt").write_text("mine")

    result = await fs_tools(workspace).delete_dir({"path": "."})

    assert not result.success
    assert result.error == "blocked: cannot delete an allowed root"
    assert (workspace / "keep.txt").read_text() == "mine"


async def test_delete_dir_refuses_the_trash_itself(workspace: Path):
    # Emptying the trash entry by entry is fine; removing the container is not —
    # it would destroy every recoverable delete at once, and the mechanism too.
    tools = fs_tools(workspace)
    _tree(workspace, files={"doomed/a.txt": "a"})
    assert (await tools.delete_dir({"path": "doomed"})).success

    result = await tools.delete_dir({"path": TRASH_DIR_NAME})

    assert not result.success
    assert result.error == "blocked: cannot delete the workspace trash itself"
    assert len(list((workspace / TRASH_DIR_NAME).iterdir())) == 1


async def test_delete_dir_refuses_a_file(workspace: Path):
    (workspace / "notes.md").write_text("keep")

    result = await fs_tools(workspace).delete_dir({"path": "notes.md"})

    assert not result.success
    assert result.error == "blocked: not a directory"
    assert (workspace / "notes.md").read_text() == "keep"


async def test_delete_dir_refuses_a_symlink_to_a_directory(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    result = await fs_tools(workspace).delete_dir({"path": "escape"})

    assert not result.success
    assert (outside / "secret.txt").read_text() == "classified"


async def test_delete_dir_outside_the_jail_is_blocked(workspace: Path, tmp_path: Path):
    victim = tmp_path / "outside"
    victim.mkdir()
    (victim / "secret.txt").write_text("classified")

    result = await fs_tools(workspace).delete_dir({"path": "../outside"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"
    assert (victim / "secret.txt").read_text() == "classified"


async def test_delete_dir_missing_directory_is_a_failed_result(workspace: Path):
    result = await fs_tools(workspace).delete_dir({"path": "nope"})

    assert not result.success
    assert result.error == "blocked: directory not found"


async def test_delete_dir_over_a_cap_leaves_the_tree_intact(workspace: Path):
    # The walk runs before anything moves, so an over-budget tree is refused
    # while it is still entirely there.
    _tree(workspace, files={"doomed/a.txt": "a", "doomed/b.txt": "b", "doomed/c.txt": "c"})

    result = await fs_tools(workspace, max_file_count=2).delete_dir({"path": "doomed"})

    assert not result.success
    assert _relative_contents(workspace / "doomed") == {"a.txt": "a", "b.txt": "b", "c.txt": "c"}
    assert not (workspace / TRASH_DIR_NAME).exists()


async def test_deleting_a_tree_already_in_the_trash_unlinks_it(workspace: Path):
    tools = fs_tools(workspace)
    _tree(workspace, files={"doomed/a.txt": "a"})
    await tools.delete_dir({"path": "doomed"})
    trashed = next((workspace / TRASH_DIR_NAME).iterdir())

    result = await tools.delete_dir({"path": str(trashed)})

    assert result.success
    assert _output(result)["outcome"] == "deleted"
    assert not trashed.exists()


async def test_trash_pruning_removes_whole_trashed_trees(workspace: Path):
    tools = fs_tools(workspace, trash_max_entries=1)
    for index in range(3):
        _tree(workspace, files={f"doomed{index}/a.txt": str(index)})
        assert (await tools.delete_dir({"path": f"doomed{index}"})).success

    survivors = list((workspace / TRASH_DIR_NAME).iterdir())
    assert len(survivors) == 1
    assert _relative_contents(survivors[0]) == {"a.txt": "2"}


async def test_the_trash_bound_holds_even_for_a_tree_the_caps_would_refuse(workspace: Path):
    """A tree too big to walk under the caps must still be evictable.

    Pruning it under the same caps would fail, and the trash would then grow
    past its own limit precisely because something large landed in it — the
    bound quietly stops applying exactly when it matters.
    """
    tools = fs_tools(workspace, trash_max_entries=1, max_file_count=3, hard_delete=False)
    _tree(workspace, files={"first/a.txt": "a", "first/b.txt": "b", "first/c.txt": "c"})
    assert (await tools.delete_dir({"path": "first"})).success

    # Shrink the budget below what the already-trashed tree needs, then trash
    # another entry so the bound forces the first one out.
    tools = fs_tools(workspace, trash_max_entries=1, max_file_count=1)
    (workspace / "second.txt").write_text("x")
    assert (await tools.delete_file({"path": "second.txt"})).success

    assert len(list((workspace / TRASH_DIR_NAME).iterdir())) == 1


# ---------------------------------------------------------------------------
# Default-closed posture
# ---------------------------------------------------------------------------


async def test_no_roots_refuses_every_directory_call(tmp_path: Path):
    victim = tmp_path / "tree"
    (victim / "a.txt").parent.mkdir(parents=True)
    (victim / "a.txt").write_text("x")
    tools = FsTools()  # no agent context, no operator roots

    for result in (
        await tools.make_dir({"path": str(tmp_path / "new")}),
        await tools.move({"src": str(victim), "dst": str(tmp_path / "moved")}),
        await tools.copy({"src": str(victim), "dst": str(tmp_path / "copied")}),
        await tools.delete_dir({"path": str(victim)}),
    ):
        assert not result.success
        assert result.error == "blocked: no allowed roots configured"

    assert (victim / "a.txt").read_text() == "x"
    assert not (tmp_path / "new").exists()
