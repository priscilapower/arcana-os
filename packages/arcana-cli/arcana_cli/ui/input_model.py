"""Toolkit-neutral input rules for the chat editor.

The parts of chat input editing that don't depend on any terminal toolkit: when
Enter submits versus continues a line, when a paste collapses to a placeholder
(and how placeholders expand back), and the slash commands only the session has.
The editor widget binds keys and completion onto these rules; keeping them here
means the rules are defined once and testable without a toolkit.
"""

from dataclasses import dataclass
from enum import StrEnum

# Package-internal exports — the input rules the chat editor, controller, and
# render module build on. Declared so the split doesn't read as dead code under
# strict unused-symbol checks.
__all__ = [
    "SESSION_COMMANDS",
    "SessionCommand",
    "SessionCommandName",
    "_PasteRegistry",
    "_should_collapse_paste",
    "_submits_on_enter",
    "_trailing_backslashes",
]


class SessionCommandName(StrEnum):
    """The slash commands only the session has: no ``arcana …`` command runs them."""

    HELP = "/help"
    MEMORY = "/memory"
    CARD = "/card"
    SWITCH = "/switch"
    RETRY = "/retry"
    SAVE = "/save"
    CLEAR = "/clear"
    FRESH = "/fresh"
    NO_MEMORY = "/no-memory"
    EXIT = "/exit"


@dataclass(frozen=True)
class SessionCommand:
    """A slash command only the session has (no ``arcana …`` command runs it).

    ``usage`` is how ``/help`` shows it; ``takes_agent`` marks the command whose
    argument names an agent, so it completes agent names.
    """

    name: SessionCommandName
    usage: str
    help: str
    takes_agent: bool = False


#: The session-only slash commands, in the order ``/help`` lists them. Every other
#: slash command is generated from the ``arcana`` command tree.
SESSION_COMMANDS: tuple[SessionCommand, ...] = (
    SessionCommand(SessionCommandName.HELP, "/help", "list these commands"),
    SessionCommand(SessionCommandName.MEMORY, "/memory", "show what this agent recalls from this session"),
    SessionCommand(SessionCommandName.CARD, "/card", "print the resolved card config — temperature, tone, weights"),
    SessionCommand(
        SessionCommandName.SWITCH,
        "/switch [name]",
        "load another agent in a new session (no name: pick one)",
        takes_agent=True,
    ),
    SessionCommand(SessionCommandName.RETRY, "/retry", "re-run your last message"),
    SessionCommand(SessionCommandName.SAVE, "/save", "force a session snapshot to disk now"),
    SessionCommand(SessionCommandName.CLEAR, "/clear", "clear the screen (the session is kept)"),
    SessionCommand(SessionCommandName.FRESH, "/fresh", "start a new session"),
    SessionCommand(SessionCommandName.NO_MEMORY, "/no-memory", "start a new stateless session (memory off)"),
    SessionCommand(SessionCommandName.EXIT, "/exit", "close the session and quit"),
)

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
