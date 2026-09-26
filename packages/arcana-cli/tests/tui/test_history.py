"""Tests for the per-agent input history: the FileHistory format, fail-closed I/O, recall and search."""

import datetime
import stat
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from prompt_toolkit.history import FileHistory

from arcana_cli.tui import history as history_mod
from arcana_cli.tui.history import (
    HISTORY_FILENAME,
    AgentHistory,
    HistoryCursor,
    agent_history_path,
    format_entry,
    parse_history,
)


def _ptk_entries(path: Path) -> list[str]:
    """The entries prompt_toolkit's FileHistory reads from ``path``, oldest first."""
    return list(reversed(list(FileHistory(str(path)).load_history_strings())))


# ── the file format, both ways against prompt_toolkit ───────────────────


def test_prompt_toolkit_reads_what_agent_history_writes(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    history = AgentHistory(path)
    for entry in ("hello", "two\nlines", "+starts with plus", ""):
        history.append(entry)
    assert _ptk_entries(path) == ["hello", "two\nlines", "+starts with plus"]


def test_agent_history_reads_what_prompt_toolkit_writes(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    ptk = FileHistory(str(path))
    for entry in ("deploy", "multi\nline\nentry", "héllo ✨"):
        ptk.store_string(entry)
    assert AgentHistory(path).entries == ("deploy", "multi\nline\nentry", "héllo ✨")


def test_format_entry_matches_prompt_toolkit_byte_for_byte(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    FileHistory(str(path)).store_string("a\nb")
    written = path.read_bytes()
    stamp = written.split(b"\n")[1].removeprefix(b"# ").decode()
    when = datetime.datetime.fromisoformat(stamp)
    assert format_entry("a\nb", when) == written


def test_parse_history_skips_comments_and_blank_lines():
    data = b"\n# 2026-01-01 00:00:00\n+one\n\n# 2026-01-02 00:00:00\n+two\n+three\n"
    assert parse_history(data) == ["one", "two\nthree"]


def test_parse_history_replaces_undecodable_bytes():
    assert parse_history(b"+caf\xff\n") == ["caf�"]


def test_appends_are_append_only(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    AgentHistory(path).append("first")
    before = path.read_bytes()
    AgentHistory(path).append("second")
    assert path.read_bytes().startswith(before)
    assert AgentHistory(path).entries == ("first", "second")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_a_new_history_file_is_owner_only(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    AgentHistory(path).append("secret-ish")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── fail-closed: unreadable / unwritable history degrades to in-memory ──


def test_unreadable_history_starts_empty_in_memory(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    path.mkdir()  # reading a directory raises IsADirectoryError
    history = AgentHistory(path)
    assert history.entries == ()
    assert history.path is None
    history.append("still works")
    assert history.entries == ("still works",)


def test_unwritable_history_keeps_entries_in_memory(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    history = AgentHistory(blocker / HISTORY_FILENAME)  # parent is a file: every write fails
    history.append("one")
    history.append("two")
    assert history.entries == ("one", "two")
    assert history.path is None


def test_missing_history_file_starts_empty_and_is_created_on_append(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    history = AgentHistory(path)
    assert history.entries == ()
    history.append("x")
    assert path.exists()


def test_in_memory_history_never_touches_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history = AgentHistory()
    history.append("a")
    assert history.path is None
    assert list(tmp_path.iterdir()) == []


# ── per-agent paths, and no traversal through a crafted id ─────────────


def test_history_path_is_scoped_per_agent(tmp_path):
    agent_id = uuid4()
    assert agent_history_path(agent_id, tmp_path) == tmp_path / "agents" / str(agent_id) / HISTORY_FILENAME
    assert AgentHistory.for_agent(agent_id, tmp_path).path == agent_history_path(agent_id, tmp_path)


def test_history_path_defaults_to_arcana_home(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod, "ARCANA_HOME", tmp_path)
    agent_id = uuid4()
    assert agent_history_path(agent_id) == tmp_path / "agents" / str(agent_id) / HISTORY_FILENAME


def test_string_ids_are_written_in_canonical_uuid_form(tmp_path):
    agent_id = uuid4()
    assert agent_history_path(agent_id.hex.upper(), tmp_path) == agent_history_path(agent_id, tmp_path)


@pytest.mark.parametrize(
    "crafted",
    ["../../etc/passwd", "..", "scout", f"{uuid4()}/../../x", "/tmp/evil", ""],
)
def test_history_path_rejects_a_crafted_agent_id_path_traversal(tmp_path, crafted):
    with pytest.raises(ValueError):
        agent_history_path(crafted, tmp_path)


def test_history_path_stays_inside_the_agents_dir(tmp_path):
    path = agent_history_path(str(UUID(int=0)), tmp_path).resolve()
    assert path.is_relative_to((tmp_path / "agents").resolve())


# ── append, suggest, search ──────────────────────────────────────────────


def test_append_skips_blank_and_repeated_newest_entries():
    history = AgentHistory()
    for text in ("a", "a", "", "b", "a"):
        history.append(text)
    assert history.entries == ("a", "b", "a")


@pytest.mark.parametrize(
    ("typed", "suggestion"),
    [
        ("dep", "loy the thing"),
        ("   ", ""),
        ("", ""),
        ("zzz", ""),
        ("x\nmak", "e build"),  # only the last typed line is matched
        ("sec", "ond line"),  # every line of a multi-line entry is a candidate
    ],
)
def test_suggest(typed, suggestion):
    history = AgentHistory(entries=["make build", "first line\nsecond line", "deploy the thing"])
    assert history.suggest(typed) == suggestion


def test_suggest_prefers_the_newest_match():
    history = AgentHistory(entries=["git status", "git stash"])
    assert history.suggest("git st") == "ash"


def test_search_walks_newest_to_oldest():
    history = AgentHistory(entries=["echo one", "other", "echo two"])
    assert history.search("echo") == 2
    assert history.search("echo", before=2) == 0
    assert history.search("echo", before=0) is None
    assert history.search("") is None
    assert history.search("nope") is None


# ── the Up/Down cursor ───────────────────────────────────────────────────


def test_cursor_walks_back_and_restores_the_draft():
    cursor = HistoryCursor(AgentHistory(entries=["one", "two"]))
    assert cursor.newer("draft") is None
    assert cursor.older("draft") == "two"
    assert cursor.browsing
    assert cursor.older("two") == "one"
    assert cursor.older("one") is None
    assert cursor.newer("one") == "two"
    assert cursor.newer("two") == "draft"
    assert not cursor.browsing


def test_cursor_keeps_edits_until_reset():
    cursor = HistoryCursor(AgentHistory(entries=["one", "two"]))
    cursor.older("")
    assert cursor.older("two edited") == "one"
    assert cursor.newer("one") == "two edited"
    cursor.reset()
    assert cursor.older("") == "two"


def test_cursor_on_empty_history_does_nothing():
    cursor = HistoryCursor(AgentHistory())
    assert cursor.older("draft") is None
    assert cursor.newer("draft") is None
