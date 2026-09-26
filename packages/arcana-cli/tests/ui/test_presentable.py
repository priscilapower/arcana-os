"""Tests for the output value types: Presentable/View, Verbatim, Failure, and fail()."""

import io

import pytest
import typer
from rich.console import Console
from rich.table import Table

from arcana_cli._render import EXIT_ERROR, EXIT_NOT_FOUND
from arcana_cli.ui.renderer import Failure, Presentable, Verbatim, View, fail, lines
from arcana_cli.ui.theme import dim, warn
from tests.support.renderer import RecordingRenderer


def _plain(renderable: object) -> str:
    out = io.StringIO()
    Console(file=out, width=100, color_system=None).print(renderable)
    return out.getvalue()


def test_a_view_is_presentable_with_both_views():
    table = Table()
    view = View(table, [{"id": "1"}])
    assert isinstance(view, Presentable)
    assert view.to_rich() is table
    assert view.to_json() == [{"id": "1"}]


def test_bare_renderables_and_data_are_not_presentable():
    assert not isinstance("text", Presentable)
    assert not isinstance({"a": 1}, Presentable)
    assert not isinstance(Table(), Presentable)


def test_lines_print_as_separate_prints_would():
    out = io.StringIO()
    console = Console(file=out, width=100, color_system=None)
    for line in ("\n  one", "  [bold]two[/]", "", dim("three")):
        console.print(line)
    assert _plain(lines("\n  one", "  [bold]two[/]", "", dim("three"))) == out.getvalue()


def test_failure_shows_an_error_line_then_its_details():
    failure = Failure("No agent 'ghost'.", (dim("  Run: arcana agent list"), "  1234"))
    assert _plain(failure.to_rich()) == "✗ No agent 'ghost'.\n  Run: arcana agent list\n  1234\n"
    assert failure.code == EXIT_ERROR


def test_failure_message_is_plain_text_not_markup():
    assert _plain(Failure("Tags [bold]x[/bold]").to_rich()) == "✗ Tags [bold]x[/bold]\n"


def test_failure_headline_can_be_restyled():
    assert _plain(Failure("Ambiguous: A, B", headline=warn).to_rich()) == "Ambiguous: A, B\n"


def test_failure_plain_details_drop_markup_and_indentation():
    assert Failure("x", (dim("  Run: arcana mcp list"),)).plain_details() == ["Run: arcana mcp list"]


def test_fail_reports_through_the_renderer_and_exits_with_the_code():
    r = RecordingRenderer()
    with pytest.raises(typer.Exit) as exc:
        fail(r, "No MCP server named 'x'.", dim("  Run: arcana mcp list"), code=EXIT_NOT_FOUND)
    assert exc.value.exit_code == EXIT_NOT_FOUND
    [failure] = r.errors
    assert (failure.message, failure.code) == ("No MCP server named 'x'.", EXIT_NOT_FOUND)
    assert r.emitted == [] and r.notes == []


def test_verbatim_renders_as_plain_text_where_it_can_only_be_rendered():
    assert _plain(Verbatim("# [bold]x[/]")) == "# [bold]x[/]\n"
