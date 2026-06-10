"""Tests for arcana eval commands."""

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


def test_eval_list_all_cases():
    result = runner.invoke(app, ["eval", "list"])
    assert result.exit_code == 0


def test_eval_list_cards_suite():
    result = runner.invoke(app, ["eval", "list", "--suite", "cards"])
    assert result.exit_code == 0


def test_eval_list_blending_suite():
    result = runner.invoke(app, ["eval", "list", "--suite", "blending"])
    assert result.exit_code == 0
