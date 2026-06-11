"""Tests for arcana_cli.ui.card_picker — non-TTY fallback and TTY keypress paths."""

import sys
from io import StringIO
from unittest.mock import patch

import readchar

from arcana.types.card import Card
from arcana_cli.ui.card_picker import select_card, select_cards

# ─────────────────────────── stdin helpers ───────────────────────────


class _FakeTTY(StringIO):
    """StringIO that reports itself as a TTY so _run_picker enters the Live path."""

    def isatty(self) -> bool:
        return True


class _FakeStdin(StringIO):
    """StringIO that reports itself as non-TTY so _run_picker uses _non_tty_fallback."""

    def isatty(self) -> bool:
        return False


# ─────────────────────────── non-TTY fallback ────────────────────────


def test_non_tty_select_by_key(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("the-hermit\n"))
    assert select_card("Pick") == Card.HERMIT


def test_non_tty_select_by_partial_name(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("hermit\n"))
    assert select_card("Pick") == Card.HERMIT


def test_non_tty_select_by_number(monkeypatch):
    # canonical order: 1 = The Fool
    monkeypatch.setattr(sys, "stdin", _FakeStdin("1\n"))
    assert select_card("Pick") == Card.FOOL


def test_non_tty_select_by_number_hermit(monkeypatch):
    # HERMIT is index 9 (0-based) → 1-based = 10
    monkeypatch.setattr(sys, "stdin", _FakeStdin("10\n"))
    assert select_card("Pick") == Card.HERMIT


def test_non_tty_blank_cancels(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("\n"))
    assert select_card("Pick") is None


def test_non_tty_unknown_returns_none(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("not-a-real-card\n"))
    assert select_card("Pick") is None


def test_non_tty_number_out_of_range_returns_none(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("99\n"))
    assert select_card("Pick") is None


def test_non_tty_multi_by_keys(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("the-fool, the-star\n"))
    result = select_cards("Pick")
    assert set(result) == {Card.FOOL, Card.STAR}


def test_non_tty_multi_by_numbers(monkeypatch):
    # 1 = FOOL, 2 = MAGICIAN
    monkeypatch.setattr(sys, "stdin", _FakeStdin("1, 2\n"))
    result = select_cards("Pick")
    assert Card.FOOL in result
    assert Card.MAGICIAN in result
    assert len(result) == 2


def test_non_tty_multi_blank_returns_empty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("\n"))
    assert select_cards("Pick") == []


def test_non_tty_multi_mixed_number_and_name(monkeypatch):
    # "1" = FOOL by number, "the-star" = STAR by key
    monkeypatch.setattr(sys, "stdin", _FakeStdin("1, the-star\n"))
    result = select_cards("Pick")
    assert Card.FOOL in result
    assert Card.STAR in result


# ─────────────────────────── TTY path ────────────────────────────────
#
# Live is mocked to a no-op context manager so tests don't need a real
# terminal.  readchar.readkey is replaced with an iterator of keys.


def test_tty_enter_selects_first_card(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with patch("readchar.readkey", side_effect=iter([readchar.key.ENTER])), patch("arcana_cli.ui.card_picker.Live"):
        result = select_card("Test")
    assert result == Card.FOOL


def test_tty_down_then_enter_selects_second(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with (
        patch("readchar.readkey", side_effect=iter([readchar.key.DOWN, readchar.key.ENTER])),
        patch("arcana_cli.ui.card_picker.Live"),
    ):
        result = select_card("Test")
    assert result == Card.MAGICIAN


def test_tty_up_clamps_at_zero(monkeypatch):
    """Up at top of list stays at first card."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with (
        patch("readchar.readkey", side_effect=iter([readchar.key.UP, readchar.key.ENTER])),
        patch("arcana_cli.ui.card_picker.Live"),
    ):
        result = select_card("Test")
    assert result == Card.FOOL


def test_tty_initial_positions_cursor(monkeypatch):
    """initial= pre-positions cursor; immediate Enter selects that card."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with patch("readchar.readkey", side_effect=iter([readchar.key.ENTER])), patch("arcana_cli.ui.card_picker.Live"):
        result = select_card("Test", initial=Card.HERMIT)
    assert result == Card.HERMIT


def test_tty_esc_cancels(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with patch("readchar.readkey", side_effect=iter([readchar.key.ESC])), patch("arcana_cli.ui.card_picker.Live"):
        result = select_card("Test")
    assert result is None


def test_tty_ctrl_c_cancels(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with patch("readchar.readkey", side_effect=iter([readchar.key.CTRL_C])), patch("arcana_cli.ui.card_picker.Live"):
        result = select_card("Test")
    assert result is None


# ─────────────────────────── multi-select ────────────────────────────


def test_tty_multi_space_selects_two(monkeypatch):
    """Space on first, Down, Space on second, Enter → both selected."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    keys = iter(
        [
            readchar.key.SPACE,
            readchar.key.DOWN,
            readchar.key.SPACE,
            readchar.key.ENTER,
        ]
    )
    with patch("readchar.readkey", side_effect=keys), patch("arcana_cli.ui.card_picker.Live"):
        result = select_cards("Test")
    assert set(result) == {Card.FOOL, Card.MAGICIAN}


def test_tty_multi_space_toggles_off(monkeypatch):
    """Select then deselect; Enter confirms empty list."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    keys = iter(
        [
            readchar.key.SPACE,
            readchar.key.SPACE,
            readchar.key.ENTER,
        ]
    )
    with patch("readchar.readkey", side_effect=keys), patch("arcana_cli.ui.card_picker.Live"):
        result = select_cards("Test")
    assert result == []


def test_tty_multi_esc_discards_selection(monkeypatch):
    """Space to select, Esc → returns empty (selection discarded)."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    keys = iter([readchar.key.SPACE, readchar.key.ESC])
    with patch("readchar.readkey", side_effect=keys), patch("arcana_cli.ui.card_picker.Live"):
        result = select_cards("Test")
    assert result == []


def test_tty_multi_ctrl_c_discards_selection(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    keys = iter([readchar.key.SPACE, readchar.key.CTRL_C])
    with patch("readchar.readkey", side_effect=keys), patch("arcana_cli.ui.card_picker.Live"):
        result = select_cards("Test")
    assert result == []


def test_tty_multi_initial_preselected(monkeypatch):
    """initial= pre-selects cards; Enter confirms them unchanged."""
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    with patch("readchar.readkey", side_effect=iter([readchar.key.ENTER])), patch("arcana_cli.ui.card_picker.Live"):
        result = select_cards("Test", initial=[Card.FOOL, Card.STAR])
    assert set(result) == {Card.FOOL, Card.STAR}
