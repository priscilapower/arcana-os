"""Tests for top-level CLI entry point."""

from typer.testing import CliRunner

from arcana_cli.main import app

runner = CliRunner()


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_help_lists_subcommands():
    result = runner.invoke(app, ["--help"])
    for cmd in ("agent", "cards", "connect", "run", "init", "status", "eval"):
        assert cmd in result.output
