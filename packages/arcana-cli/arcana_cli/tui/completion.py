"""Slash-command completion for the chat input, as a pure function over a :class:`SlashVocabulary`.

Completion only ever applies to a leading ``/``: ordinary prose never pops the
menu. The first word completes against the command names. After a command
group and a space (``/agent ``, ``/memory connect ``) the next word completes
against the group's actions. After a whole command, a word starting with ``-``
completes against its options, and where an argument names an agent (the
agent to ``/switch`` to, ``/agent edit <name>``, ``--agent <name>``) against
the agent names. Anything else completes nothing.

The vocabulary is plain data built once per session, so a keystroke costs a
few dictionary lookups; the agent names are only read when an agent slot is
being completed.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from arcana_cli.ui.input_model import SESSION_COMMANDS


@dataclass(frozen=True)
class CommandCompletion:
    """What completes after one command's name.

    ``options`` are the option names offered after a ``-`` (hidden ones left
    out); ``takes_value`` those of them followed by a value rather than being a
    flag. ``agent_options`` are the options whose value names an agent, and
    ``agent_argument`` the index of the positional argument that does, if one does.
    """

    options: tuple[str, ...] = ()
    takes_value: frozenset[str] = frozenset()
    agent_options: frozenset[str] = frozenset()
    agent_argument: int | None = None


@dataclass(frozen=True)
class SlashVocabulary:
    """Everything slash completion knows.

    ``names`` are the first words (``/help``, ``/agent``, ``/status``);
    ``actions`` maps a command group (``/agent``, ``/memory connect``) to the
    actions its next word completes against; ``commands`` maps a whole command
    (``/agent edit``, ``/switch``) to what completes after it.
    """

    names: tuple[str, ...] = ()
    actions: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    commands: Mapping[str, CommandCompletion] = field(default_factory=dict[str, CommandCompletion])


def session_vocabulary() -> SlashVocabulary:
    """The vocabulary of the session-only commands alone (no generated ones)."""
    return SlashVocabulary(
        names=tuple(c.name.value for c in SESSION_COMMANDS),
        commands={c.name.value: CommandCompletion(agent_argument=0) for c in SESSION_COMMANDS if c.takes_agent},
    )


@dataclass(frozen=True)
class SlashCompletions:
    """The candidates for the text before the cursor.

    Each item replaces the text from index ``start`` (into the text before the
    cursor) up to the cursor. ``items`` is empty when nothing completes.
    """

    start: int
    items: tuple[str, ...]


def _positional_index(words: Sequence[str], takes_value: frozenset[str]) -> int:
    """How many positional arguments ``words`` (the complete words after a command) hold."""
    count = 0
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = word in takes_value
        elif word:
            count += 1
    return count


def slash_completions(
    text_before_cursor: str, vocabulary: SlashVocabulary, agent_names: Callable[[], Iterable[str]]
) -> SlashCompletions:
    """Complete ``text_before_cursor`` against ``vocabulary``; ``agent_names`` is read only for an agent slot."""
    text = text_before_cursor
    none = SlashCompletions(start=len(text), items=())
    if not text.startswith("/"):
        return none
    words = text.split(" ")
    if len(words) == 1:
        return SlashCompletions(start=0, items=tuple(n for n in vocabulary.names if n.startswith(text)))

    def candidates(pool: Iterable[str]) -> SlashCompletions:
        word = words[-1]
        return SlashCompletions(start=len(text) - len(word), items=tuple(c for c in pool if c.startswith(word)))

    command, rest = words[0], 1
    while command in vocabulary.actions:
        if rest == len(words) - 1:  # the action itself is being typed
            return candidates(vocabulary.actions[command])
        command, rest = f"{command} {words[rest]}", rest + 1
    spec = vocabulary.commands.get(command)
    if spec is None:
        return none
    word, before = words[-1], words[rest:-1]
    if word.startswith("-"):
        return candidates(spec.options)
    if before and before[-1] in spec.agent_options:
        return candidates(agent_names())
    if before and before[-1] in spec.takes_value:
        return none
    if spec.agent_argument is not None and _positional_index(before, spec.takes_value) == spec.agent_argument:
        return candidates(agent_names())
    return none
