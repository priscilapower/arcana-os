"""Tests for the filesystem builtin handlers. No LLM.

Every jail is a ``tmp_path``, never ``$HOME``. The handlers must always return a
``ToolResult`` — a refusal is a failed result, never an exception — and a refused
call must leave the filesystem exactly as it was.
"""

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from arcana.tools.builtins.fs.config import TRASH_DIR_NAME, FsToolsConfig, agent_workspace
from arcana.tools.builtins.fs.handlers import FsTools
from arcana.types.tool import ToolResult
from tests.support.tools import fs_tools


def _output(result: ToolResult) -> dict[str, Any]:
    """The success payload, narrowed — every handler returns a JSON object."""
    assert isinstance(result.output, dict)
    return result.output


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


async def test_list_dir_defaults_to_the_workspace_root(workspace: Path):
    # The discovery entry point: with no arguments an agent can see what it has.
    (workspace / "notes.md").write_text("hello")
    (workspace / "sub").mkdir()

    result = await fs_tools(workspace).list_dir({})

    assert result.success
    output = _output(result)
    assert output["path"] == str(workspace)
    assert [(e["name"], e["kind"]) for e in output["entries"]] == [("notes.md", "file"), ("sub", "dir")]
    assert output["truncated"] is False


async def test_list_dir_reports_sizes_and_sorts_by_name(workspace: Path):
    (workspace / "b.txt").write_text("xyz")
    (workspace / "a.txt").write_text("hello")

    output = _output(await fs_tools(workspace).list_dir({"path": "."}))

    assert [(e["name"], e["size"]) for e in output["entries"]] == [("a.txt", 5), ("b.txt", 3)]


async def test_list_dir_lists_a_subdirectory(workspace: Path):
    (workspace / "sub").mkdir()
    (workspace / "sub" / "deep.txt").write_text("x")

    output = _output(await fs_tools(workspace).list_dir({"path": "sub"}))

    assert [e["name"] for e in output["entries"]] == ["deep.txt"]


async def test_list_dir_is_not_recursive(workspace: Path):
    (workspace / "sub").mkdir()
    (workspace / "sub" / "deep.txt").write_text("x")

    output = _output(await fs_tools(workspace).list_dir({}))

    # The subdirectory is named, never descended into.
    assert [e["name"] for e in output["entries"]] == ["sub"]


async def test_list_dir_names_a_symlink_without_following_it(workspace: Path, tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    (workspace / "escape").symlink_to(secret)

    output = _output(await fs_tools(workspace).list_dir({}))

    # Described as a link, never resolved to its out-of-jail target.
    assert [(e["name"], e["kind"]) for e in output["entries"]] == [("escape", "symlink")]


async def test_list_dir_classifies_non_regular_entries_as_other(workspace: Path):
    os.mkfifo(workspace / "pipe")

    output = _output(await fs_tools(workspace).list_dir({}))

    assert [(e["name"], e["kind"]) for e in output["entries"]] == [("pipe", "other")]


async def test_list_dir_caps_entries_and_flags_truncation(workspace: Path):
    for index in range(10):
        (workspace / f"f{index}.txt").write_text("x")

    output = _output(await fs_tools(workspace, max_list_entries=3).list_dir({}))

    assert len(output["entries"]) == 3
    assert output["truncated"] is True


async def test_list_dir_refuses_a_file(workspace: Path):
    (workspace / "notes.md").write_text("hello")

    result = await fs_tools(workspace).list_dir({"path": "notes.md"})

    assert not result.success
    assert result.error == "blocked: not a directory"


async def test_list_dir_outside_the_jail_is_blocked(workspace: Path):
    result = await fs_tools(workspace).list_dir({"path": "../.."})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"


async def test_list_dir_through_a_symlinked_directory_is_blocked(workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)

    result = await fs_tools(workspace).list_dir({"path": "escape"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"


async def test_list_dir_missing_directory_is_a_failed_result(workspace: Path):
    result = await fs_tools(workspace).list_dir({"path": "nope"})

    assert not result.success
    assert result.error == "file not found"


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


async def test_read_file_returns_text(workspace: Path):
    (workspace / "notes.md").write_text("hello world")

    result = await fs_tools(workspace).read_file({"path": "notes.md"})

    assert result.success
    assert result.output == {
        "path": str(workspace / "notes.md"),
        "text": "hello world",
        "truncated": False,
        "encoding": "utf-8",
    }


async def test_read_file_truncates_past_the_cap(workspace: Path):
    (workspace / "big.txt").write_text("x" * 500)

    result = await fs_tools(workspace, max_read_bytes=100).read_file({"path": "big.txt"})

    assert result.success
    assert _output(result)["text"] == "x" * 100
    assert _output(result)["truncated"] is True


async def test_read_file_refuses_binary_rather_than_returning_bytes(workspace: Path):
    (workspace / "image.bin").write_bytes(b"\xff\xfe\x00\x01 not utf-8")

    result = await fs_tools(workspace).read_file({"path": "image.bin"})

    assert not result.success
    assert result.error is not None
    assert "not valid UTF-8" in result.error


async def test_read_file_outside_the_jail_is_blocked(workspace: Path, tmp_path: Path):
    (tmp_path / "secret.txt").write_text("classified")

    result = await fs_tools(workspace).read_file({"path": "../secret.txt"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"


async def test_read_file_missing_file_is_a_failed_result(workspace: Path):
    result = await fs_tools(workspace).read_file({"path": "nope.txt"})

    assert not result.success
    assert result.error == "file not found"


async def test_read_file_missing_path_argument(workspace: Path):
    result = await fs_tools(workspace).read_file({})

    assert not result.success
    assert result.error == "blocked: missing 'path'"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


async def test_write_file_creates_and_reports_bytes(workspace: Path):
    result = await fs_tools(workspace).write_file({"path": "out.txt", "content": "hi"})

    assert result.success
    assert result.output == {"path": str(workspace / "out.txt"), "bytes_written": 2, "mode": "create"}
    assert (workspace / "out.txt").read_text() == "hi"


async def test_write_file_create_refuses_to_clobber(workspace: Path):
    (workspace / "out.txt").write_text("original")

    result = await fs_tools(workspace).write_file({"path": "out.txt", "content": "new"})

    assert not result.success
    assert result.error is not None
    assert "already exists" in result.error
    assert (workspace / "out.txt").read_text() == "original"


async def test_write_file_overwrite_replaces(workspace: Path):
    (workspace / "out.txt").write_text("original")

    result = await fs_tools(workspace).write_file({"path": "out.txt", "content": "new", "mode": "overwrite"})

    assert result.success
    assert (workspace / "out.txt").read_text() == "new"


async def test_write_file_append_adds_to_the_end(workspace: Path):
    (workspace / "log.txt").write_text("one\n")

    result = await fs_tools(workspace).write_file({"path": "log.txt", "content": "two\n", "mode": "append"})

    assert result.success
    assert (workspace / "log.txt").read_text() == "one\ntwo\n"


async def test_write_file_append_to_a_missing_file_creates_it(workspace: Path):
    result = await fs_tools(workspace).write_file({"path": "log.txt", "content": "one", "mode": "append"})

    assert result.success
    assert (workspace / "log.txt").read_text() == "one"


async def test_write_file_over_the_cap_touches_nothing(workspace: Path):
    result = await fs_tools(workspace, max_write_bytes=10).write_file({"path": "big.txt", "content": "x" * 50})

    assert not result.success
    assert result.error is not None
    assert "over the 10-byte write cap" in result.error
    assert not (workspace / "big.txt").exists()


async def test_write_file_append_refuses_when_the_existing_file_is_over_the_cap(workspace: Path):
    # Appending by read-modify-write must never silently truncate the original.
    target = workspace / "log.txt"
    target.write_text("x" * 50)

    result = await fs_tools(workspace, max_write_bytes=10).write_file(
        {"path": "log.txt", "content": "y", "mode": "append"}
    )

    assert not result.success
    assert target.read_text() == "x" * 50


async def test_write_file_creates_nested_directories_inside_the_jail(workspace: Path):
    result = await fs_tools(workspace).write_file({"path": "a/b/c.txt", "content": "deep"})

    assert result.success
    assert (workspace / "a" / "b" / "c.txt").read_text() == "deep"


async def test_write_file_outside_the_jail_is_blocked(workspace: Path, tmp_path: Path):
    result = await fs_tools(workspace).write_file({"path": "../escape.txt", "content": "x"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"
    assert not (tmp_path / "escape.txt").exists()


async def test_write_file_rejects_an_unknown_mode(workspace: Path):
    result = await fs_tools(workspace).write_file({"path": "f.txt", "content": "x", "mode": "clobber"})

    assert not result.success
    assert result.error is not None
    assert "invalid 'mode'" in result.error
    assert not (workspace / "f.txt").exists()


async def test_write_file_requires_string_content(workspace: Path):
    result = await fs_tools(workspace).write_file({"path": "f.txt", "content": 42})

    assert not result.success
    assert result.error == "missing 'content'"


async def test_write_file_refuses_a_directory(workspace: Path):
    (workspace / "sub").mkdir()

    result = await fs_tools(workspace).write_file({"path": "sub", "content": "x"})

    assert not result.success
    assert result.error == "blocked: path is a directory"


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


async def test_delete_file_soft_deletes_into_the_trash(workspace: Path):
    target = workspace / "doomed.txt"
    target.write_text("bye")

    result = await fs_tools(workspace).delete_file({"path": "doomed.txt"})

    assert result.success
    assert _output(result)["outcome"] == "trashed"
    assert not target.exists()

    # Recoverable: the bytes are still there, under the trash.
    trashed = list((workspace / TRASH_DIR_NAME).iterdir())
    assert len(trashed) == 1
    assert trashed[0].read_text() == "bye"


async def test_delete_file_hard_delete_unlinks(workspace: Path):
    target = workspace / "doomed.txt"
    target.write_text("bye")

    result = await fs_tools(workspace, hard_delete=True).delete_file({"path": "doomed.txt"})

    assert result.success
    assert _output(result)["outcome"] == "deleted"
    assert not target.exists()
    assert not (workspace / TRASH_DIR_NAME).exists()


async def test_delete_file_refuses_a_directory(workspace: Path):
    (workspace / "sub").mkdir()

    result = await fs_tools(workspace).delete_file({"path": "sub"})

    assert not result.success
    assert result.error == "blocked: path is a directory"
    assert (workspace / "sub").is_dir()


async def test_delete_file_refuses_a_non_regular_file(workspace: Path):
    fifo = workspace / "pipe"
    os.mkfifo(fifo)

    result = await fs_tools(workspace).delete_file({"path": "pipe"})

    assert not result.success
    assert result.error == "blocked: not a regular file"
    assert fifo.exists()


async def test_delete_file_outside_the_jail_is_blocked(workspace: Path, tmp_path: Path):
    victim = tmp_path / "secret.txt"
    victim.write_text("classified")

    result = await fs_tools(workspace).delete_file({"path": "../secret.txt"})

    assert not result.success
    assert result.error == "blocked: path outside allowed roots"
    assert victim.exists()


async def test_delete_file_missing_file_is_a_failed_result(workspace: Path):
    result = await fs_tools(workspace).delete_file({"path": "nope.txt"})

    assert not result.success
    assert result.error == "blocked: file not found"


async def test_repeated_deletes_of_the_same_name_do_not_collide_in_the_trash(workspace: Path):
    tools = fs_tools(workspace)
    for body in ("first", "second"):
        (workspace / "note.txt").write_text(body)
        assert (await tools.delete_file({"path": "note.txt"})).success

    trashed = sorted(p.read_text() for p in (workspace / TRASH_DIR_NAME).iterdir())
    assert trashed == ["first", "second"]


async def test_trash_is_pruned_to_the_configured_bound(workspace: Path):
    tools = fs_tools(workspace, trash_max_entries=2)
    for index in range(5):
        (workspace / f"f{index}.txt").write_text(str(index))
        assert (await tools.delete_file({"path": f"f{index}.txt"})).success

    assert len(list((workspace / TRASH_DIR_NAME).iterdir())) == 2


async def test_trash_pruning_evicts_by_deletion_time_not_file_mtime(workspace: Path):
    # os.replace carries the original mtime into the trash, so an old file
    # deleted last must still survive a bound that evicts by deletion order.
    stale = workspace / "stale.txt"
    stale.write_text("written long ago")
    os.utime(stale, (0, 0))
    fresh = workspace / "fresh.txt"
    fresh.write_text("written just now")

    tools = fs_tools(workspace, trash_max_entries=1)
    assert (await tools.delete_file({"path": "fresh.txt"})).success
    assert (await tools.delete_file({"path": "stale.txt"})).success

    survivors = [p.read_text() for p in (workspace / TRASH_DIR_NAME).iterdir()]
    assert survivors == ["written long ago"]


async def test_deleting_a_file_already_in_the_trash_unlinks_it(workspace: Path):
    tools = fs_tools(workspace)
    (workspace / "note.txt").write_text("x")
    await tools.delete_file({"path": "note.txt"})
    trashed = next((workspace / TRASH_DIR_NAME).iterdir())

    result = await tools.delete_file({"path": str(trashed)})

    assert result.success
    assert _output(result)["outcome"] == "deleted"
    assert not trashed.exists()


# ---------------------------------------------------------------------------
# Default-closed posture
# ---------------------------------------------------------------------------


async def test_no_roots_refuses_every_filesystem_call(tmp_path: Path):
    victim = tmp_path / "f.txt"
    victim.write_text("x")
    tools = FsTools()  # no agent context, no operator roots

    for result in (
        await tools.read_file({"path": str(victim)}),
        await tools.write_file({"path": str(victim), "content": "y", "mode": "overwrite"}),
        await tools.delete_file({"path": str(victim)}),
    ):
        assert not result.success
        assert result.error == "blocked: no allowed roots configured"

    assert victim.read_text() == "x"


def test_for_agent_jails_to_the_agent_workspace(tmp_path: Path):
    agent_id = uuid4()
    config = FsToolsConfig.for_agent(agent_id, home=tmp_path)

    assert config.allowed_roots == [agent_workspace(agent_id, home=tmp_path)]
    # Never ~/.arcana itself, and never the secrets directory.
    root = config.allowed_roots[0]
    assert root.is_relative_to(tmp_path / "agents")
    assert not (tmp_path / "secrets").is_relative_to(root)


def test_for_agent_without_an_agent_has_no_roots():
    assert FsToolsConfig.for_agent(None).allowed_roots == []
