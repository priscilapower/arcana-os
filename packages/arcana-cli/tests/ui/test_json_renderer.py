"""Tests for JsonRenderer — the --json scripting contract behind the renderer port."""

import io
import json
from uuid import UUID

import pytest
import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from rich.text import Text
from typer.testing import CliRunner

from arcana_cli._async import run_async
from arcana_cli._render import EXIT_ERROR, EXIT_NOT_FOUND, emit_json
from arcana_cli.ui.renderer import Choice, Failure, JsonRenderer, NonInteractiveError, Question, View


class _Model(BaseModel):
    id: UUID
    name: str


def _renderer() -> tuple[JsonRenderer, io.StringIO]:
    stderr = io.StringIO()
    return JsonRenderer(stderr=Console(file=stderr, width=200)), stderr


def test_emit_dict_is_the_emit_json_document(capsys: pytest.CaptureFixture[str]):
    payload = {"name": "hermit", "id": UUID(int=1), "tags": ["a"]}
    emit_json(payload)
    expected = capsys.readouterr().out
    JsonRenderer().emit(payload)
    assert capsys.readouterr().out == expected


def test_emit_list(capsys: pytest.CaptureFixture[str]):
    JsonRenderer().emit([1, 2])
    assert json.loads(capsys.readouterr().out) == [1, 2]


def test_emit_pydantic_model_dumps_json_mode(capsys: pytest.CaptureFixture[str]):
    JsonRenderer().emit(_Model(id=UUID(int=7), name="x"))
    assert json.loads(capsys.readouterr().out) == {"id": str(UUID(int=7)), "name": "x"}


@pytest.mark.parametrize("renderable", [Text("hi"), Table(), "[bold]markup[/]"])
def test_emit_rejects_rich_renderables(renderable: object, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(TypeError, match="JsonRenderer.emit takes a dict, list, or pydantic model"):
        JsonRenderer().emit(renderable)
    assert capsys.readouterr().out == ""


def _error(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """The one error document the stream carries; nothing else is on stdout."""
    return json.loads(capsys.readouterr().out)["error"]


async def test_ask_fails_closed_naming_the_flag(capsys: pytest.CaptureFixture[str]):
    r, stderr = _renderer()
    with pytest.raises(NonInteractiveError) as exc:
        await r.ask(Question("Agent name", flag="--name"))
    assert exc.value.exit_code == EXIT_ERROR
    error = _error(capsys)
    assert error["code"] == EXIT_ERROR
    assert "pass --name instead" in str(error["message"])
    assert stderr.getvalue() == ""


async def test_ask_secret_never_leaks_its_default(capsys: pytest.CaptureFixture[str]):
    r, stderr = _renderer()
    with pytest.raises(NonInteractiveError) as exc:
        await r.ask(Question("API key", default="sk-live-123", secret=True))
    assert "sk-live-123" not in capsys.readouterr().out
    assert "sk-live-123" not in stderr.getvalue()
    assert "sk-live-123" not in str(exc.value)


async def test_confirm_fails_closed(capsys: pytest.CaptureFixture[str]):
    r, _ = _renderer()
    with pytest.raises(NonInteractiveError):
        await r.confirm("Remove it?", flag="--yes")
    assert "pass --yes instead" in str(_error(capsys)["message"])


async def test_refusal_message_is_not_markup(capsys: pytest.CaptureFixture[str]):
    r, _ = _renderer()
    with pytest.raises(NonInteractiveError):
        await r.ask(Question("Tags [comma-separated]"))
    assert "[comma-separated]" in str(_error(capsys)["message"])


async def test_select_fails_closed(capsys: pytest.CaptureFixture[str]):
    r, _ = _renderer()
    with pytest.raises(NonInteractiveError):
        await r.select([Choice(1, "one")], title="Pick a number", flag="--number")
    assert "Pick a number" in str(_error(capsys)["message"])


def test_error_is_the_error_document_with_plain_details(capsys: pytest.CaptureFixture[str]):
    r, stderr = _renderer()
    r.error(Failure("No agent 'ghost'.", ("[dim]  Run: arcana agent list[/]",), code=EXIT_NOT_FOUND))
    assert json.loads(capsys.readouterr().out) == {
        "error": {"code": EXIT_NOT_FOUND, "message": "No agent 'ghost'.", "details": ["Run: arcana agent list"]}
    }
    assert stderr.getvalue() == ""


def test_error_without_details_has_code_and_message_only(capsys: pytest.CaptureFixture[str]):
    JsonRenderer().error(Failure("Prompt cannot be empty."))
    assert json.loads(capsys.readouterr().out) == {"error": {"code": EXIT_ERROR, "message": "Prompt cannot be empty."}}


def test_emit_presentable_is_its_json_view(capsys: pytest.CaptureFixture[str]):
    JsonRenderer().emit(View(Table(), [{"id": "1"}]))
    assert json.loads(capsys.readouterr().out) == [{"id": "1"}]


async def test_status_prints_nothing(capsys: pytest.CaptureFixture[str]):
    async with JsonRenderer().status("working") as status:
        status.stop()
    assert capsys.readouterr() == ("", "")


def test_note_goes_to_stderr_off_the_json_stream(capsys: pytest.CaptureFixture[str]):
    r, stderr = _renderer()
    r.note("routed to scout")
    assert capsys.readouterr().out == ""
    assert "routed to scout" in stderr.getvalue()


def test_stream_has_no_json_form():
    with pytest.raises(TypeError, match="no stream"):
        JsonRenderer().stream()


def test_non_interactive_error_exits_with_error_code_through_typer():
    app = typer.Typer()

    @app.command()
    def cmd() -> None:
        run_async(JsonRenderer().ask(Question("Agent name", flag="--name")))

    result = CliRunner().invoke(app, [])
    assert result.exit_code == EXIT_ERROR
    assert "pass --name instead" in json.loads(result.stdout)["error"]["message"]
    assert result.stderr == ""


async def test_waiting_instructions_go_to_stderr_never_the_json_stream(capsys: pytest.CaptureFixture[str]):
    r, stderr = _renderer()
    async with r.waiting("Waiting for authorization…") as wait:
        wait.show(Text("enter code ABCD"))
    assert capsys.readouterr().out == ""
    assert "enter code ABCD" in stderr.getvalue()
    assert "Waiting for authorization…" in stderr.getvalue()
