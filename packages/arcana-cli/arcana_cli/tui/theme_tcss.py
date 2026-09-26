"""The Textual stylesheet and theme, generated from the design tokens in :mod:`arcana_cli.ui.theme`.

``theme.py`` stays the one source of colour. Every module-level ``#rrggbb``
string constant there becomes a TCSS variable named after it (``ACCENT`` →
``$accent``, ``SURFACE_HI`` → ``$surface-hi``), so adding or recolouring a token
in ``theme.py`` reaches the stylesheet with no second edit.

The variables are published twice, from the same mapping:

* :data:`ARCANA_TCSS` — the app's ``CSS``: the variable declarations followed by
  the hand-written chrome rules, which may only reference those variables.
* :data:`ARCANA_THEME` — a Textual :class:`~textual.theme.Theme` carrying the same
  variables, so Textual's built-in widgets (``Input``, ``OptionList``, …), whose
  own stylesheets read the theme rather than the app's ``CSS``, are on-palette
  too. Its semantic colours (``primary``, ``error``, …) map onto the tokens.

Both are built at import: there is no generated file to keep in sync.
"""

import re

from textual.theme import Theme

from arcana_cli.ui import theme

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_VARIABLE_REF = re.compile(r"\$([a-z0-9-]+)")


def variable_name(token: str) -> str:
    """The TCSS variable name (without ``$``) for a ``theme.py`` constant name."""
    return token.lower().replace("_", "-")


def color_tokens() -> dict[str, str]:
    """Every module-level ``#rrggbb`` string constant in ``theme.py``, keyed by constant name."""
    return {
        name: value
        for name, value in vars(theme).items()
        if name.isupper() and isinstance(value, str) and _HEX_COLOR.fullmatch(value)
    }


def tcss_variables() -> dict[str, str]:
    """The TCSS variables (name without ``$`` → hex) generated from :func:`color_tokens`."""
    return {variable_name(name): value for name, value in color_tokens().items()}


#: Hand-written chrome: layout, borders, focus. Colours come only from the
#: generated variables (the drift test checks every ``$reference`` resolves).
CHROME_TCSS = """
Screen {
    background: $surface;
    color: $txt;
}

Transcript {
    height: 1fr;
    background: $surface;
    scrollbar-size-vertical: 1;
    scrollbar-color: $txt3;
    scrollbar-background: $surface;
}

Screen:inline Transcript {
    height: auto;
    max-height: 70vh;
}

LiveBlock {
    height: auto;
    display: none;
    color: $txt;
}

LiveBlock.-active {
    display: block;
}

StatusBar {
    height: 1;
    color: $txt2;
}

ChatInputPanel {
    height: auto;
}

ChatInput, ChatInput:focus {
    height: auto;
    max-height: 8;
    border: none;
    padding: 0;
    background: $surface;
    color: $txt;
}

ChatInput .chat-input--prompt {
    color: $accent;
    text-style: bold;
}

ChatInput .chat-input--continuation {
    color: $txt3;
}

ChatInput .text-area--suggestion {
    color: $txt3;
}

CompletionMenu {
    display: none;
    width: auto;
    height: auto;
    background: $surface-hi;
    color: $txt2;
}

CompletionMenu.-open {
    display: block;
}

CompletionMenu .completion-menu--highlight {
    background: $accent;
    color: $surface;
    text-style: bold;
}

SearchBar {
    display: none;
    height: 1;
    color: $txt2;
}

SearchBar.-active {
    display: block;
}

ModalScreen {
    align: center middle;
}

ModalScreen:inline {
    height: auto;
}

.dialog {
    width: 80;
    max-width: 100%;
    height: auto;
    border: round $accent;
    background: $surface-hi;
    padding: 0 1;
}

.dialog-title {
    color: $accent;
    text-style: bold;
}

.dialog-hint {
    color: $txt3;
}

.dialog-error {
    color: $red;
}

.dialog Input {
    border: tall $txt3;
    background: $surface;
}

.dialog Input:focus {
    border: tall $accent;
}

.dialog OptionList, .dialog SelectionList {
    height: auto;
    max-height: 12;
    border: none;
    background: $surface-hi;
}

.card-picker {
    width: 100%;
    height: 26;
    max-height: 100vh;
    background: $surface;
}

.card-picker-list {
    width: 1fr;
    min-width: 28;
    max-width: 44;
    height: 100%;
    border: round $picker-border;
    padding: 0 1;
}

.card-picker-list Input {
    background: $surface;
    color: $txt;
}

.card-picker-list OptionList {
    height: 1fr;
    border: none;
    padding: 0;
    background: $surface;
    scrollbar-size-vertical: 1;
    scrollbar-color: $picker-hint-dim;
    scrollbar-background: $surface;
}

.card-picker-list OptionList > .option-list--option-highlighted,
.card-picker-list OptionList > .option-list--option-hover {
    background: $surface;
    color: $txt;
    text-style: none;
}

.card-picker-preview {
    width: 2fr;
    height: 100%;
    scrollbar-size-vertical: 1;
    scrollbar-color: $picker-hint-dim;
    scrollbar-background: $surface;
}
"""


def chrome_references() -> set[str]:
    """Every ``$variable`` the chrome rules reference (names without ``$``)."""
    return set(_VARIABLE_REF.findall(CHROME_TCSS))


def _declarations(variables: dict[str, str]) -> str:
    return "\n".join(f"${name}: {value};" for name, value in variables.items())


ARCANA_TCSS: str = f"{_declarations(tcss_variables())}\n{CHROME_TCSS}"

ARCANA_THEME = Theme(
    name="arcana",
    primary=theme.ACCENT,
    secondary=theme.AMBER,
    accent=theme.ACCENT,
    warning=theme.ORANGE,
    error=theme.RED,
    success=theme.GREEN,
    foreground=theme.TXT,
    background=theme.SURFACE,
    surface=theme.SURFACE,
    panel=theme.SURFACE_HI,
    dark=True,
    variables=tcss_variables(),
)
