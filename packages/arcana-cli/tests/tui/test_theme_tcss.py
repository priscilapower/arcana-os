"""Tests for the stylesheet and theme generated from ui/theme.py — the no-drift guarantee."""

import re

from arcana_cli.tui import theme_tcss
from arcana_cli.tui.theme_tcss import (
    ARCANA_TCSS,
    ARCANA_THEME,
    chrome_references,
    color_tokens,
    tcss_variables,
    variable_name,
)
from arcana_cli.ui import theme

_DECLARATION = re.compile(r"^\$([a-z0-9-]+): (#[0-9a-fA-F]{6});$", re.MULTILINE)


def _theme_hex_constants() -> dict[str, str]:
    """Every module-level ``#rrggbb`` constant in theme.py, found independently of the generator."""
    source = open(theme.__file__, encoding="utf-8").read()
    return dict(re.findall(r'^([A-Z][A-Z0-9_]*) = "(#[0-9a-fA-F]{6})"', source, re.MULTILINE))


def test_variable_names_are_lower_kebab():
    assert variable_name("ACCENT") == "accent"
    assert variable_name("SURFACE_HI") == "surface-hi"
    assert variable_name("TXT2") == "txt2"


def test_every_theme_token_is_declared_in_the_tcss():
    declared = dict(_DECLARATION.findall(ARCANA_TCSS))
    for name, value in _theme_hex_constants().items():
        assert declared.get(variable_name(name)) == value, f"{name} missing or different in the generated tcss"


def test_every_declared_variable_comes_from_a_theme_token():
    declared = dict(_DECLARATION.findall(ARCANA_TCSS))
    expected = {variable_name(name): value for name, value in color_tokens().items()}
    assert declared == expected


def test_generator_sees_exactly_the_hex_literals_in_theme_py():
    # Aliases (PICKER_BORDER = ACCENT) are tokens too, so compare against values bound at import.
    literals = _theme_hex_constants()
    tokens = color_tokens()
    assert literals.keys() <= tokens.keys()
    assert all(tokens[name] == getattr(theme, name) for name in tokens)


def test_chrome_references_only_generated_variables():
    missing = chrome_references() - tcss_variables().keys()
    assert not missing, f"chrome rules reference undefined variables: {sorted(missing)}"


def test_the_textual_theme_carries_the_same_variables():
    assert ARCANA_THEME.variables == tcss_variables()
    assert ARCANA_THEME.primary == theme.ACCENT
    assert ARCANA_THEME.surface == theme.SURFACE
    assert ARCANA_THEME.dark is True


def test_chrome_rules_follow_the_declarations():
    assert ARCANA_TCSS.endswith(theme_tcss.CHROME_TCSS)
