"""Tests for the two-pane picker: :class:`CardPickerScreen` driven by Pilot, and :func:`pick` on the caller's loop."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest
from rich.panel import Panel
from textual.pilot import Pilot

from arcana.types.card import Card
from arcana_cli.tui.card_picker import NO_MATCHES, CardPickerScreen, PickerApp, pick
from arcana_cli.ui.card_picker import card_choices
from arcana_cli.ui.renderer import Choice
from tests.support.tui import DEFAULT_SIZE, run_inline_headless

CARDS = card_choices()


def _index(card: Card, choices: Sequence[Choice[Any]] = CARDS) -> int:
    return next(i for i, c in enumerate(choices) if c.value is card)


class _Picker:
    """A running :class:`PickerApp` and its screen."""

    def __init__(self, app: PickerApp, pilot: Pilot[list[int] | None], screen: CardPickerScreen) -> None:
        self.app = app
        self.pilot = pilot
        self.screen = screen

    async def press(self, *keys: str) -> None:
        await self.pilot.press(*keys)
        await self.pilot.pause()

    async def type(self, text: str) -> None:
        await self.press(*text)

    def visible(self) -> list[Card]:
        return [CARDS[i].value for i in self.screen.listed]

    def hint(self) -> str:
        return str(self.screen.query_one("#picker-hint").render())

    def title(self) -> str:
        return str(self.screen.query_one("#picker-title").render())

    def row(self, card: Card) -> str:
        """The rendered text of ``card``'s row (it must be visible)."""
        row = self.visible().index(card)
        return str(self.screen.options.get_option_at_index(row).prompt)


@asynccontextmanager
async def picker(choices: Sequence[Choice[Any]] = CARDS, **kwargs: Any) -> AsyncIterator[_Picker]:
    screen = CardPickerScreen(choices, **kwargs)
    app = PickerApp(screen)
    async with app.run_test(size=DEFAULT_SIZE) as pilot:
        await pilot.pause()
        yield _Picker(app, pilot, screen)


# ── list, cursor and preview ──────────────────────────────────────────────


async def test_the_list_shows_every_choice_and_the_preview_follows_the_cursor():
    async with picker() as p:
        assert p.visible() == [c.value for c in CARDS]
        assert p.screen.highlighted == 0
        assert p.screen.preview.content is CARDS[0].preview
        await p.press("down", "down")
        assert p.screen.highlighted == 2
        assert p.screen.preview.content is CARDS[2].preview
        await p.press("up")
        assert p.screen.preview.content is CARDS[1].preview


async def test_the_cursor_stops_at_either_end():
    async with picker() as p:
        await p.press("up")
        assert p.screen.highlighted == 0
        await p.press(*["down"] * (len(CARDS) + 3))
        assert p.screen.highlighted == len(CARDS) - 1


async def test_the_cursor_row_carries_the_arrow():
    async with picker() as p:
        assert p.row(Card.FOOL).startswith("▶")
        assert not p.row(Card.MAGICIAN).startswith("▶")


async def test_initial_positions_the_cursor():
    async with picker(initial=[_index(Card.HERMIT)]) as p:
        assert p.screen.highlighted == _index(Card.HERMIT)
        assert p.screen.preview.content is CARDS[_index(Card.HERMIT)].preview


async def test_the_title_is_shown():
    async with picker(title="Choose a primary card") as p:
        assert p.title() == "Choose a primary card"


# ── filter ────────────────────────────────────────────────────────────────


async def test_typing_filters_and_backspace_widens():
    async with picker() as p:
        await p.type("the h")
        assert p.visible() == [Card.HIGH_PRIESTESS, Card.HIEROPHANT, Card.HERMIT, Card.HANGED_MAN]
        await p.type("e")
        assert p.visible() == [Card.HERMIT]
        assert p.screen.preview.content is CARDS[_index(Card.HERMIT)].preview
        await p.press("backspace")
        assert len(p.visible()) == 4
        await p.press(*["backspace"] * 5)
        assert len(p.visible()) == len(CARDS)


async def test_the_filter_matches_a_cards_key():
    async with picker() as p:
        await p.type("hanged-man")
        assert p.visible() == [Card.HANGED_MAN]


async def test_no_matches_shows_a_placeholder_and_enter_does_nothing():
    async with picker() as p:
        await p.type("zzz")
        assert p.visible() == []
        assert p.screen.highlighted is None
        preview = p.screen.preview.content
        assert isinstance(preview, Panel)
        assert str(preview.renderable) == NO_MATCHES
        await p.press("enter")
        assert p.app.is_running


async def test_a_slash_is_not_typed_into_the_filter():
    async with picker() as p:
        await p.type("/her")
        assert p.screen.filter.value == "her"


# ── single pick ───────────────────────────────────────────────────────────


async def test_enter_picks_the_highlighted_choice():
    async with picker() as p:
        await p.press("down", "enter")
    assert p.app.return_value == [1]


async def test_enter_picks_from_the_filtered_list():
    async with picker() as p:
        await p.type("star")
        await p.press("enter")
    assert p.app.return_value == [_index(Card.STAR)]


async def test_space_types_into_the_filter_in_a_single_pick():
    async with picker() as p:
        await p.type("wheel of")
        assert p.screen.filter.value == "wheel of"
        assert p.visible() == [Card.WHEEL_OF_FORTUNE]
        await p.press(*["backspace"] * 8)
        await p.type("wheelof")  # spaces are optional
        assert p.visible() == [Card.WHEEL_OF_FORTUNE]


async def test_a_click_picks_the_clicked_row():
    async with picker() as p:
        await p.pilot.click("#picker-options", offset=(2, 2))
        await p.pilot.pause()
    assert p.app.return_value == [2]


@pytest.mark.parametrize("key", ["escape", "ctrl+c"])
async def test_escape_and_ctrl_c_cancel(key: str):
    async with picker() as p:
        await p.press("down", key)
    assert p.app.return_value is None


# ── multi-select ──────────────────────────────────────────────────────────


async def test_space_toggles_and_enter_confirms_in_list_order():
    async with picker(multi=True) as p:
        await p.press("down", "down", "space", "up", "space")
        assert p.screen.selected == [1, 2]
        assert "✓" in p.row(Card.HIGH_PRIESTESS)
        assert "✓" not in p.row(Card.FOOL)
        assert "(2 selected)" in p.title()
        await p.press("space")  # untoggle the Magician
        assert p.screen.selected == [2]
        await p.press("enter")
    assert p.app.return_value == [2]


async def test_initial_pre_selects_in_a_multi_select():
    initial = [_index(Card.STAR), _index(Card.FOOL)]
    async with picker(multi=True, initial=initial) as p:
        assert p.screen.selected == sorted(initial)
        assert p.screen.highlighted == _index(Card.STAR)
        await p.press("enter")
    assert p.app.return_value == sorted(initial)


async def test_max_items_refuses_a_further_space_with_a_hint():
    async with picker(multi=True, max_items=2) as p:
        assert "max 2" in p.hint()
        await p.press("space", "down", "space", "down", "space")
        assert p.screen.selected == [0, 1]
        assert "Limit reached (2)" in p.hint()
        assert "(2/2 selected)" in p.title()
        await p.press("up", "space")  # deselecting clears the hint and frees a slot
        assert "Limit reached" not in p.hint()
        await p.press("down", "space")
        assert p.screen.selected == [0, 2]


async def test_the_selection_survives_filtering():
    async with picker(multi=True) as p:
        await p.type("sun")
        await p.press("space")
        await p.press(*["backspace"] * 3)
        await p.type("moon")
        await p.press("space")
        await p.type("zzz")
        await p.press("enter")  # confirms even with nothing matching
    assert p.app.return_value == sorted([_index(Card.SUN), _index(Card.MOON)])


async def test_in_a_multi_select_space_toggles_and_the_filter_ignores_spaces():
    async with picker(multi=True) as p:
        await p.type("highpri")
        assert p.visible() == [Card.HIGH_PRIESTESS]
        await p.press("space")
        assert p.screen.filter.value == "highpri"
        assert p.screen.selected == [_index(Card.HIGH_PRIESTESS)]


async def test_a_click_toggles_in_a_multi_select():
    async with picker(multi=True) as p:
        await p.pilot.click("#picker-options", offset=(2, 1))
        await p.pilot.pause()
        assert p.screen.selected == [1]
        assert p.app.focused is p.screen.filter


@pytest.mark.parametrize("key", ["escape", "ctrl+c"])
async def test_cancelling_a_multi_select_returns_none(key: str):
    async with picker(multi=True) as p:
        await p.press("space", key)
    assert p.app.return_value is None


# ── choices ───────────────────────────────────────────────────────────────


async def test_disabled_choices_are_left_out():
    choices = [Choice("a", "Alpha"), Choice("b", "Beta", disabled=True), Choice("c", "Gamma")]
    async with picker(choices, initial=[1]) as p:
        assert [str(o.prompt).strip("▶ ") for o in p.screen.options.options] == ["Alpha", "Gamma"]
        assert p.screen.highlighted == 0
        await p.press("down", "enter")
    assert p.app.return_value == [2]


async def test_a_choice_without_a_preview_previews_its_label():
    async with picker([Choice("a", "Alpha")]) as p:
        preview = p.screen.preview.content
        assert isinstance(preview, Panel)
        assert "Alpha" in str(preview.renderable)


async def test_the_default_card_choices_leave_the_world_out():
    async with picker() as p:
        await p.type("world")
        assert p.visible() == []


# ── pick: the one-shot picker app ─────────────────────────────────────────


async def test_pick_runs_on_the_callers_loop_and_returns_the_indexes(monkeypatch: pytest.MonkeyPatch):
    async def session(pilot: Pilot[Any]) -> None:
        await pilot.pause()
        await pilot.press(*"hermit", "enter")

    run_inline_headless(monkeypatch, PickerApp, session)
    loop = asyncio.get_running_loop()
    factory = loop.get_task_factory()
    assert await pick(CARDS, title="Pick") == [_index(Card.HERMIT)]
    assert asyncio.get_running_loop() is loop
    assert loop.get_task_factory() is factory


async def test_pick_cancel_returns_an_empty_list(monkeypatch: pytest.MonkeyPatch):
    async def session(pilot: Pilot[Any]) -> None:
        await pilot.pause()
        await pilot.press("escape")

    run_inline_headless(monkeypatch, PickerApp, session)
    assert await pick(CARDS, multi=True) == []
