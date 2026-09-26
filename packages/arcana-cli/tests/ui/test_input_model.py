"""Tests for the toolkit-neutral input rules: when Enter submits, and big-paste collapsing."""

import pytest

from arcana_cli.ui.input_model import _PasteRegistry, _should_collapse_paste, _submits_on_enter, _trailing_backslashes


@pytest.mark.parametrize(
    ("text", "count"),
    [("", 0), ("hi", 0), ("hi\\", 1), ("hi\\\\", 2), ("a\\\\\\", 3)],
)
def test_trailing_backslashes(text, count):
    assert _trailing_backslashes(text) == count


@pytest.mark.parametrize(
    ("text", "submits"),
    [
        ("", True),
        ("hello", True),
        ("hello\\", False),  # one unescaped backslash → continuation
        ("hello\\\\", True),  # escaped pair → submit
        ("hello\\\\\\", False),  # odd → continuation
    ],
)
def test_submits_on_enter(text, submits):
    assert _submits_on_enter(text) is submits


@pytest.mark.parametrize(
    ("text", "collapses"),
    [("one line", False), ("a\nb", False), ("a\nb\nc", False), ("a\nb\nc\nd", True), ("\n".join("x" * 9), True)],
)
def test_should_collapse_paste(text, collapses):
    assert _should_collapse_paste(text) is collapses


def test_paste_registry_roundtrip():
    pastes = _PasteRegistry()
    blob = "\n".join(f"line {i}" for i in range(6))
    label = pastes.collapse(blob)
    assert label == "[pasted 6 lines]"
    assert pastes.expand(f"see {label} please") == f"see {blob} please"


def test_paste_registry_disambiguates_same_size():
    pastes = _PasteRegistry()
    a = "\n".join(["a"] * 6)
    b = "\n".join(["b"] * 6)
    label_a = pastes.collapse(a)
    label_b = pastes.collapse(b)
    assert label_a != label_b
    assert pastes.expand(f"{label_a}|{label_b}") == f"{a}|{b}"
