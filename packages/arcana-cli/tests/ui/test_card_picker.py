"""Tests for arcana_cli.ui.card_picker — card choices, and select_card / select_cards over the renderer port."""

import pytest

import arcana_cli.ui.card_picker as card_picker
from arcana.types.card import Card
from arcana_cli.ui.card_picker import card_choices, select_card, select_cards
from arcana_cli.ui.renderer import NonInteractiveError, TtyRenderer
from tests.support.renderer import RecordingRenderer

# ── the choices ───────────────────────────────────────────────────────────


def test_card_choices_leave_the_world_out_by_default():
    values = [c.value for c in card_choices()]
    assert Card.WORLD not in values
    assert values == [c for c in Card if c is not Card.WORLD]


def test_card_choices_offer_the_world_only_when_the_caller_lets_it_in():
    assert Card.WORLD in [c.value for c in card_choices(exclude=())]


def test_card_choices_honour_exclude():
    values = [c.value for c in card_choices(exclude={Card.FOOL, Card.WORLD})]
    assert Card.FOOL not in values
    assert Card.WORLD not in values
    assert len(values) == len(Card) - 2


def test_card_choices_carry_a_label_and_a_preview():
    hermit = next(c for c in card_choices() if c.value is Card.HERMIT)
    assert hermit.label == "IX. The Hermit"
    assert hermit.preview is not None


# ── select_card ───────────────────────────────────────────────────────────


async def test_select_card_returns_the_pick():
    r = RecordingRenderer(selections=[Card.HERMIT])
    assert await select_card("Pick", renderer=r) is Card.HERMIT
    (options,) = r.select_options
    assert options["title"] == "Pick"
    assert options["multi"] is False
    assert Card.WORLD not in [c.value for c in r.offered[0]]


async def test_select_card_cancel_returns_none():
    assert await select_card(renderer=RecordingRenderer(selections=[None])) is None


async def test_select_card_passes_initial_and_flag():
    r = RecordingRenderer(selections=[Card.HERMIT])
    await select_card("Pick", initial=Card.HERMIT, flag="--card", renderer=r)
    assert r.select_options[0]["initial"] == [Card.HERMIT]
    assert r.select_options[0]["flag"] == "--card"


async def test_select_card_exclude_hides_cards():
    r = RecordingRenderer(selections=[Card.SUN])
    await select_card(exclude={Card.FOOL, Card.WORLD}, renderer=r)
    offered = [c.value for c in r.offered[0]]
    assert Card.FOOL not in offered
    assert Card.WORLD not in offered


async def test_select_card_defaults_to_the_terminal_renderer(monkeypatch: pytest.MonkeyPatch):
    built: list[RecordingRenderer] = []

    def fake_tty() -> RecordingRenderer:
        built.append(RecordingRenderer(selections=[Card.STAR]))
        return built[-1]

    monkeypatch.setattr(card_picker, "TtyRenderer", fake_tty)
    assert await select_card() is Card.STAR
    assert len(built) == 1


# ── select_cards ──────────────────────────────────────────────────────────


async def test_select_cards_returns_the_picks():
    r = RecordingRenderer(selections=[[Card.FOOL, Card.STAR]])
    assert await select_cards("Pick", max_items=3, renderer=r) == [Card.FOOL, Card.STAR]
    (options,) = r.select_options
    assert options == {"multi": True, "initial": [], "title": "Pick", "max_items": 3, "flag": None}


async def test_select_cards_cancel_returns_an_empty_list():
    assert await select_cards(renderer=RecordingRenderer(selections=[None])) == []


async def test_select_cards_pre_selects_initial():
    r = RecordingRenderer(selections=[[Card.FOOL]])
    await select_cards(initial=[Card.FOOL, Card.STAR], renderer=r)
    assert r.select_options[0]["initial"] == [Card.FOOL, Card.STAR]


async def test_select_cards_hides_the_world_by_default():
    r = RecordingRenderer(selections=[[]])
    await select_cards(renderer=r)
    assert Card.WORLD not in [c.value for c in r.offered[0]]


async def test_select_cards_exclude_hides_cards():
    r = RecordingRenderer(selections=[[]])
    await select_cards(exclude={Card.FOOL, Card.WORLD}, renderer=r)
    assert Card.FOOL not in [c.value for c in r.offered[0]]


# ── without a terminal ────────────────────────────────────────────────────


async def test_without_a_terminal_the_picker_fails_closed_naming_the_flag(capsys: pytest.CaptureFixture[str]):
    # pytest's captured stdin is not a terminal.
    with pytest.raises(NonInteractiveError) as exc:
        await select_card("Choose a card", flag="--card", renderer=TtyRenderer())
    assert exc.value.flag == "--card"
    assert "pass --card instead" in capsys.readouterr().err
