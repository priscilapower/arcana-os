"""Tests for the RecordingRenderer harness itself — command tests rely on its strictness."""

import pytest
from rich.text import Text

from arcana_cli.ui.renderer import Choice, Question, Renderer
from tests.support.renderer import RecordingRenderer


def test_is_a_renderer():
    r: Renderer = RecordingRenderer()
    assert r is not None


def test_text_renders_what_was_emitted():
    r = RecordingRenderer()
    r.emit(Text("one"))
    r.emit("[bold]two[/bold]")
    assert r.text() == "one\ntwo\n"


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
