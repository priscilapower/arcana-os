"""Input editing for the chat REPL — the prompt_toolkit buffer layer.

This module owns everything about *getting a line out of the user* with
prompt_toolkit: the Enter / newline / paste key bindings, slash-command
completion, and the per-agent arrow-key history. The toolkit-neutral rules they
apply (when Enter submits, when a paste collapses, the slash-command list) live
in :mod:`arcana_cli.ui.input_model`. It has no dependency on the transcript or
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
from arcana_cli.ui.input_model import _SLASH_NAMES, _PasteRegistry, _should_collapse_paste, _submits_on_enter

# Package-internal exports — the prompt_toolkit input layer the controller, app,
# and render module build on. Declared so the split doesn't read as dead code
# under strict unused-symbol checks.
__all__ = [
    "_SlashCompleter",
    "_agent_history",
    "_build_key_bindings",
]


def _insert_newline(event: KeyPressEvent) -> None:
    event.current_buffer.insert_text("\n")


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
