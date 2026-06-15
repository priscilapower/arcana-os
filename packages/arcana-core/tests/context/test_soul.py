"""Tests for arcana.context.soul.read_soul."""

from arcana.context.soul import read_soul


def test_missing_file_returns_none(tmp_path):
    assert read_soul(tmp_path / "nonexistent.md") is None


def test_present_file_returns_content(tmp_path):
    soul = tmp_path / "soul.md"
    soul.write_text("## Preferences\n- concise by default", encoding="utf-8")
    assert read_soul(soul) == "## Preferences\n- concise by default"


def test_empty_file_returns_none(tmp_path):
    soul = tmp_path / "soul.md"
    soul.write_text("", encoding="utf-8")
    assert read_soul(soul) is None


def test_whitespace_only_returns_none(tmp_path):
    soul = tmp_path / "soul.md"
    soul.write_text("   \n\n  ", encoding="utf-8")
    assert read_soul(soul) is None


def test_unreadable_file_returns_none(tmp_path):
    soul = tmp_path / "soul.md"
    soul.write_text("content", encoding="utf-8")
    soul.chmod(0o000)
    try:
        assert read_soul(soul) is None
    finally:
        soul.chmod(0o644)
