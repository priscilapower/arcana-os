"""Slash-command completion for the chat input, as a pure function.

Completion only ever applies to a leading ``/``: ordinary prose never pops the
menu. The first word completes against the slash-command names; after
``/switch`` and a space, the tail completes against agent names. Any other
command with arguments completes nothing.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

#: The one command whose argument completes: the agent to switch to.
SWITCH_COMMAND = "/switch"


@dataclass(frozen=True)
class SlashCompletions:
    """The candidates for the text before the cursor.

    Each item replaces the text from index ``start`` (into the text before the
    cursor) up to the cursor. ``items`` is empty when nothing completes.
    """

    start: int
    items: tuple[str, ...]


def slash_completions(
    text_before_cursor: str,
    commands: Sequence[str],
    agent_names: Callable[[], Iterable[str]],
) -> SlashCompletions:
    """Complete ``text_before_cursor`` against ``commands``, or agent names after ``/switch``.

    ``agent_names`` is only called for a ``/switch <tail>`` completion, so an
    agent registry behind it is not read on every keystroke.
    """
    none = SlashCompletions(start=len(text_before_cursor), items=())
    if not text_before_cursor.startswith("/"):
        return none
    head, sep, tail = text_before_cursor.partition(" ")
    if head == SWITCH_COMMAND and sep:
        names = tuple(name for name in agent_names() if name.startswith(tail))
        return SlashCompletions(start=len(head) + len(sep), items=names)
    if sep:  # a completed command with arguments that don't complete
        return none
    return SlashCompletions(start=0, items=tuple(name for name in commands if name.startswith(text_before_cursor)))
