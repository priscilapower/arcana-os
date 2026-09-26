"""Tests for the RecordingRenderer harness itself — command tests rely on its strictness."""

import pytest
import typer
from rich.text import Text

from arcana_cli.ui.renderer import Choice, Question, Renderer, View, fail
from tests.support.renderer import RecordingRenderer


def test_is_a_renderer():
    r: Renderer = RecordingRenderer()
    assert r is not None


def test_text_renders_what_was_emitted():
    r = RecordingRenderer()
    r.emit(Text("one"))
    r.emit("[bold]two[/bold]")
    assert r.text() == "one\ntwo\n"


def test_a_presentable_is_text_by_its_human_view_and_a_document_by_its_json_view():
    r = RecordingRenderer()
    r.emit(View("[bold]human[/]", {"json": True}))
    r.emit({"bare": 1})
    r.emit(Text("human only"))
    assert r.text() == "human\n{'bare': 1}\nhuman only\n"
    assert r.documents() == [{"json": True}, {"bare": 1}]


def test_a_failure_is_recorded_apart_from_the_output():
    r = RecordingRenderer()
    with pytest.raises(typer.Exit):
        fail(r, "No agent 'ghost'.", "  hint")
    assert r.emitted == []
    assert r.errors_text() == "✗ No agent 'ghost'.\n  hint\n"


async def test_ask_answers_from_the_script_and_records_the_question():
    r = RecordingRenderer(answers=["hermit"])
    q = Question("Name")
    assert await r.ask(q) == "hermit"
    assert r.questions == [q]


async def test_ask_validator_rejection_consumes_the_next_answer():
    r = RecordingRenderer(answers=["", "ok"])
    assert await r.ask(Question("Name", validator=lambda v: None if v else "empty")) == "ok"
    assert r.rejections == ["empty"]


async def test_unscripted_questions_fail_loudly():
    r = RecordingRenderer()
    with pytest.raises(AssertionError, match="unscripted ask"):
        await r.ask(Question("Name"))
    with pytest.raises(AssertionError, match="unscripted confirm"):
        await r.confirm("Sure?")
    with pytest.raises(AssertionError, match="unscripted select"):
        await r.select([Choice(1, "one")])


async def test_confirm_answers_from_the_script():
    r = RecordingRenderer(confirms=[False])
    assert await r.confirm("Remove?") is False
    assert r.confirmations == ["Remove?"]


async def test_select_returns_the_scripted_value():
    r = RecordingRenderer(selections=["b", ["a", "b"], None])
    choices = [Choice("a", "A"), Choice("b", "B")]
    assert await r.select(choices) == "b"
    assert await r.select(choices, multi=True) == ["a", "b"]
    assert await r.select(choices) is None
    assert len(r.offered) == 3


async def test_select_rejects_a_value_that_was_not_offered():
    r = RecordingRenderer(selections=["z"])
    with pytest.raises(AssertionError, match="not offered"):
        await r.select([Choice("a", "A")])


async def test_select_rejects_a_disabled_value():
    r = RecordingRenderer(selections=["a"])
    with pytest.raises(AssertionError, match="disabled"):
        await r.select([Choice("a", "A", disabled=True)])


async def test_select_multi_respects_max_items():
    r = RecordingRenderer(selections=[["a", "b"]])
    with pytest.raises(AssertionError, match="max_items"):
        await r.select([Choice("a", "A"), Choice("b", "B")], multi=True, max_items=1)


async def test_status_and_stream_are_recorded():
    r = RecordingRenderer()
    async with r.status("working"):
        pass
    async with r.stream(prefix=Text("» ")) as sink:
        sink.write("hi ")
        sink.write("there")
    assert r.statuses == ["working"]
    assert r.streamed == ["hi ", "there"]


async def test_stream_with_render_emits_the_finished_block():
    r = RecordingRenderer()
    async with r.stream(prefix=Text("» "), render=lambda t: Text(t.upper())) as sink:
        sink.write("hi")
    assert r.streamed == ["hi"]
    assert "HI" in r.text()


async def test_notes_are_kept_apart_from_output():
    r = RecordingRenderer()
    r.emit(Text("reply"))
    r.note(Text("routed to scout"))
    assert "routed" not in r.text()
    assert "routed to scout" in r.notes_text()


async def test_events_order_a_stopped_status_before_the_chunks():
    r = RecordingRenderer()
    async with r.stream() as sink, r.status("thinking") as status:
        status.stop()
        sink.write("a")
        status.stop()
        sink.write("b")
    assert r.events == [("status", "thinking"), ("stop", "thinking"), ("chunk", "a"), ("chunk", "b")]


async def test_a_status_stops_when_its_block_ends():
    r = RecordingRenderer()
    async with r.status("working"):
        pass
    assert r.events == [("status", "working"), ("stop", "working")]


async def test_a_blank_answer_takes_the_default():
    r = RecordingRenderer(answers=["", ""])
    assert await r.ask(Question("Name", default="scout")) == "scout"
    assert await r.ask(Question("Tags")) == ""
