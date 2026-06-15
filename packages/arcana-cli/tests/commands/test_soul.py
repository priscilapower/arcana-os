"""Tests for arcana soul edit and arcana soul show commands."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.soul as soul_mod
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    home = tmp_path / ".arcana"
    home.mkdir()
    monkeypatch.setattr(soul_mod, "ARCANA_HOME", home)
    monkeypatch.setattr(soul_mod, "_SOUL_PATH", home / "soul.md")
    return home


# ---------------------------------------------------------------------------
# arcana soul show
# ---------------------------------------------------------------------------


def test_show_prints_contents_when_file_exists(arcana_home):
    (arcana_home / "soul.md").write_text("## Preferences\n- concise", encoding="utf-8")
    result = runner.invoke(app, ["soul", "show"])
    assert result.exit_code == 0
    assert "## Preferences" in result.output
    assert "concise" in result.output


def test_show_prints_hint_when_missing(arcana_home):
    result = runner.invoke(app, ["soul", "show"])
    assert result.exit_code == 0
    assert "soul edit" in result.output


# ---------------------------------------------------------------------------
# arcana soul edit
# ---------------------------------------------------------------------------


def test_edit_creates_template_when_missing(arcana_home):
    soul_path = arcana_home / "soul.md"
    assert not soul_path.exists()

    with patch("arcana_cli.commands.soul.click.edit"):
        result = runner.invoke(app, ["soul", "edit"])

    assert result.exit_code == 0
    assert soul_path.exists()
    content = soul_path.read_text(encoding="utf-8")
    assert "## About me" in content
    assert "## Preferences" in content


def test_edit_does_not_overwrite_existing_file(arcana_home):
    soul_path = arcana_home / "soul.md"
    soul_path.write_text("my custom content", encoding="utf-8")

    with patch("arcana_cli.commands.soul.click.edit"):
        result = runner.invoke(app, ["soul", "edit"])

    assert result.exit_code == 0
    assert soul_path.read_text(encoding="utf-8") == "my custom content"


def test_edit_errors_when_arcana_not_initialised(tmp_path, monkeypatch):
    monkeypatch.setattr(soul_mod, "ARCANA_HOME", tmp_path / "nonexistent")
    monkeypatch.setattr(soul_mod, "_SOUL_PATH", tmp_path / "nonexistent" / "soul.md")
    result = runner.invoke(app, ["soul", "edit"])
    assert result.exit_code != 0
