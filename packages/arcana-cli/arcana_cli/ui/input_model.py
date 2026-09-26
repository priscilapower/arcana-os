"""Toolkit-neutral input rules for the chat editor.

The parts of chat input editing that don't depend on any terminal toolkit: when
Enter submits versus continues a line, when a paste collapses to a placeholder
(and how placeholders expand back), and the in-session slash-command vocabulary.
The editor widget binds keys and completion onto these rules; keeping them here
means the rules are defined once and testable without a toolkit.
"""

from enum import StrEnum

# Package-internal exports — the input rules the chat editor, controller, and
# render module build on. Declared so the split doesn't read as dead code under
# strict unused-symbol checks.
__all__ = [
    "_SLASH_COMMANDS",
    "_SLASH_NAMES",
    "_SLASH_SUBCOMMANDS",
    "WizardGroup",
    "_PasteRegistry",
    "_should_collapse_paste",
    "_submits_on_enter",
    "_trailing_backslashes",
]

# In-session slash commands (name → help text). Shared by `/help` and completion.
_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "list these commands"),
    ("/memory", "show what this agent recalls from this session"),
    ("/card", "print the resolved card config — temperature, tone, weights"),
    ("/switch [name]", "load another agent in a new session (no name: pick one)"),
    ("/retry", "re-run your last message"),
    ("/save", "force a session snapshot to disk now"),
    ("/clear", "clear the screen (the session is kept)"),
    ("/fresh", "start a new session"),
    ("/no-memory", "start a new stateless session (memory off)"),
    ("/agent create|edit|delete", "create, edit or delete an agent (same options as arcana agent …)"),
    ("/providers add|edit|remove|login", "set up a model provider (same options as arcana providers …)"),
    ("/mcp add|approve|remove", "connect an MCP server; its tools reach this session once approved"),
    ("/exit", "close the session and quit"),
]
_SLASH_NAMES: list[str] = [name.split(" ")[0] for name, _ in _SLASH_COMMANDS]


class WizardGroup(StrEnum):
    """The slash commands that take a sub-action, each running ``arcana <group> <action>``."""

    AGENT = "/agent"
    PROVIDERS = "/providers"
    MCP = "/mcp"


# The sub-actions of each wizard command, for completion and dispatch.
_SLASH_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    WizardGroup.AGENT: ("create", "edit", "delete"),
    WizardGroup.PROVIDERS: ("add", "edit", "remove", "login"),
    WizardGroup.MCP: ("add", "approve", "remove"),
}

# A pasted chunk spanning at least this many lines is collapsed to a placeholder.
_PASTE_COLLAPSE_MIN_LINES = 4


def _trailing_backslashes(text: str) -> int:
    """Count the run of ``\\`` immediately before the cursor."""
    count = 0
    for ch in reversed(text):
        if ch == "\\":
            count += 1
        else:
            break
    return count


def _submits_on_enter(text_before_cursor: str) -> bool:
    """Whether pressing Enter should submit.

    Enter submits unless the text ends with an *unescaped* backslash — an odd
    number of trailing backslashes means the final one is a line-continuation, so
    Enter inserts a newline instead. An even count (including zero) submits.
    """
    return _trailing_backslashes(text_before_cursor) % 2 == 0


def _should_collapse_paste(text: str) -> bool:
    """Collapse multi-line pastes; small ones are inserted inline as typed."""
    return text.count("\n") + 1 >= _PASTE_COLLAPSE_MIN_LINES


class _PasteRegistry:
    """Maps collapsed-paste placeholders back to their full text for one message.

    A big paste is shown in the editor as ``[pasted N lines]`` while the real text
    is kept here; :meth:`expand` restores it just before the turn goes to the model.
    Placeholders are per-message — :meth:`clear` resets the map each read.
    """

    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._counter = 0

    def collapse(self, text: str) -> str:
        """Store *text* and return a unique ``[pasted N lines]`` placeholder."""
        self._counter += 1
        lines = text.count("\n") + 1
        label = f"[pasted {lines} lines]"
        if label in self._map:  # disambiguate repeat pastes of the same size
            label = f"[pasted {lines} lines · {self._counter}]"
        self._map[label] = text
        return label

    def expand(self, text: str) -> str:
        """Replace any placeholders in *text* with their stored full paste."""
        for label, full in self._map.items():
            text = text.replace(label, full)
        return text

    def clear(self) -> None:
        self._map.clear()
        self._counter = 0
