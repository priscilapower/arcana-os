"""Input editing for the chat REPL — the prompt_toolkit buffer layer.

This module owns everything about *getting a line out of the user*: the Enter /
newline / paste key bindings, big-paste collapsing, slash-command completion,
and the per-agent arrow-key history. It has no dependency on the transcript or
the controller, so the editor behaviour is testable on its own.

The input editor: Enter submits, ``\\``+Enter / Alt+Enter insert a newline, with
arrow-key history and slash/agent tab-completion.
"""

import contextlib
from collections.abc import Iterator
from uuid import UUID

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions, is_searching
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys

from arcana.agents.registry import AgentRegistry
from arcana_cli.constants import ARCANA_HOME

# Package-internal exports — the input layer the controller, app, and render
# module build on. Declared so the split doesn't read as dead code under strict
# unused-symbol checks.
__all__ = [
    "_SLASH_COMMANDS",
    "_PasteRegistry",
    "_SlashCompleter",
    "_agent_history",
    "_build_key_bindings",
    "_should_collapse_paste",
    "_submits_on_enter",
    "_trailing_backslashes",
]

# In-session slash commands (name → help text). Shared by `/help` and completion.
_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "list these commands"),
    ("/memory", "show what this agent recalls from this session"),
    ("/card", "print the resolved card config — temperature, tone, weights"),
    ("/switch <name>", "load another agent in a new session"),
    ("/retry", "re-run your last message"),
    ("/save", "force a session snapshot to disk now"),
    ("/clear", "clear the transcript (history is kept)"),
    ("/fresh", "start a new session"),
    ("/no-memory", "start a new stateless session (memory off)"),
    ("/exit", "close the session and quit"),
]
_SLASH_NAMES: list[str] = [name.split(" ")[0] for name, _ in _SLASH_COMMANDS]

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


def _insert_newline(event: KeyPressEvent) -> None:
    event.current_buffer.insert_text("\n")


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


def _build_key_bindings(pastes: _PasteRegistry) -> KeyBindings:
    """Editor key bindings shared by the input control (and the editor tests).

    Enter submits (via ``validate_and_handle``, which routes through the buffer's
    accept handler); ``\\``+Enter, Alt+Enter, and Ctrl+J insert a newline; big
    pastes collapse to a placeholder; Ctrl+L repaints.
    """
    kb = KeyBindings()

    # ``~is_searching`` keeps this off during Ctrl+R reverse-search, where Enter
    # must accept the match (prompt_toolkit's default) rather than submit here.
    @kb.add("enter", filter=~is_searching)
    def _(event: KeyPressEvent) -> None:
        buf = event.current_buffer
        if _submits_on_enter(buf.document.text_before_cursor):
            buf.validate_and_handle()
        else:
            # Drop the trailing "\" continuation marker, then break the line.
            buf.delete_before_cursor(1)
            buf.insert_text("\n")

    @kb.add(Keys.BracketedPaste)
    def _(event: KeyPressEvent) -> None:
        data = event.data
        text = pastes.collapse(data) if _should_collapse_paste(data) else data
        event.current_buffer.insert_text(text)

    @kb.add("c-l")
    def _(event: KeyPressEvent) -> None:
        event.app.renderer.clear()  # Ctrl+L repaints the screen

    @kb.add("tab")
    def _(event: KeyPressEvent) -> None:
        # Tab opens the slash-command menu (and cycles through it once open).
        buf = event.current_buffer
        if buf.complete_state:
            buf.complete_next()
        else:
            buf.start_completion(select_first=False)

    # Esc closes the completion menu. Gated on `has_completions` so it doesn't
    # shadow the Alt+Enter (Esc+Enter) newline chord when no menu is open.
    @kb.add("escape", filter=has_completions)
    def _(event: KeyPressEvent) -> None:
        event.current_buffer.cancel_completion()

    # Explicit newline chords for users who prefer them over "\". Shift+Enter is
    # not a distinct terminal key, but terminals that emit Esc+Enter for it land
    # on the Alt+Enter binding.
    kb.add("escape", "enter")(_insert_newline)  # Alt / Option + Enter
    kb.add("c-j")(_insert_newline)  # Ctrl + J

    return kb


class _SlashCompleter(Completer):
    """Tab-completes slash-command names, and agent names after ``/switch``."""

    def __init__(self, reg: AgentRegistry) -> None:
        self.reg = reg

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterator[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        head, sep, tail = text.partition(" ")
        if head == "/switch" and sep:
            for record in self.reg.list():
                if record.name.startswith(tail):
                    yield Completion(record.name, start_position=-len(tail))
            return
        if sep:  # a completed command with args we don't complete
            return
        for name in _SLASH_NAMES:
            if name.startswith(text):
                yield Completion(name, start_position=-len(text))


def _agent_history(agent_id: UUID) -> History:
    """Per-agent input history — arrow-key recall is scoped to one agent.

    Falls back to an in-memory history if the on-disk file can't be opened, so
    the editor still works (just without persistence) on a read-only home.
    """
    with contextlib.suppress(OSError):
        return FileHistory(str(ARCANA_HOME / "agents" / str(agent_id) / "chat_history"))
    return InMemoryHistory()
