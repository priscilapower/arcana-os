"""Tests for slash-command completion (the pure function behind the chat input's menu)."""

import pytest

from arcana_cli.tui.completion import (
    CommandCompletion,
    SlashCompletions,
    SlashVocabulary,
    session_vocabulary,
    slash_completions,
)
from arcana_cli.ui.input_model import SESSION_COMMANDS

AGENTS = ("scout", "scribe", "oracle")

#: A small vocabulary shaped like the generated one: groups, a nested group, a top-level command.
VOCABULARY = SlashVocabulary(
    names=(*session_vocabulary().names, "/agent", "/mcp", "/status"),
    actions={
        "/agent": ("create", "edit", "delete"),
        "/mcp": ("add", "approve"),
        "/memory": ("list", "connect"),
        "/memory connect": ("obsidian", "markdown"),
    },
    commands={
        **session_vocabulary().commands,
        "/agent create": CommandCompletion(options=("--card", "--model", "--name"), takes_value=frozenset({"--name"})),
        "/agent edit": CommandCompletion(
            options=("--card", "--name"), takes_value=frozenset({"--card", "--name", "-c"}), agent_argument=0
        ),
        "/mcp add": CommandCompletion(options=("--name", "--url"), takes_value=frozenset({"--name", "--url"})),
        "/memory list": CommandCompletion(
            options=("--agent", "--limit"),
            takes_value=frozenset({"--agent", "-a", "--limit"}),
            agent_options=frozenset({"--agent", "-a"}),
        ),
        "/memory connect obsidian": CommandCompletion(options=("--name", "--vault")),
        "/status": CommandCompletion(),
    },
)


def _complete(text: str) -> SlashCompletions:
    return slash_completions(text, VOCABULARY, lambda: AGENTS)


def test_completes_command_names():
    assert _complete("/me") == SlashCompletions(start=0, items=("/memory",))


def test_a_bare_slash_offers_every_command():
    assert _complete("/").items == VOCABULARY.names


def test_the_session_vocabulary_is_the_session_commands():
    assert session_vocabulary().names == tuple(c.name.value for c in SESSION_COMMANDS)


def test_completes_agent_names_after_switch():
    assert _complete("/switch sc") == SlashCompletions(start=len("/switch "), items=("scout", "scribe"))
    assert _complete("/switch ").items == AGENTS


@pytest.mark.parametrize(
    "text", ["hello", "", " /me", "hi /switch sc", "/help me", "/card x", "/switch scout x", "/status x"]
)
def test_completes_nothing_without_a_leading_slash_or_after_other_arguments(text):
    assert _complete(text).items == ()


def test_agent_names_are_only_read_for_an_agent_slot():
    calls: list[str] = []

    def names():
        calls.append("read")
        return AGENTS

    for text in ("/sw", "hello", "/agent e", "/agent edit --card ", "/memory list --limit "):
        slash_completions(text, VOCABULARY, names)
    assert calls == []
    slash_completions("/switch o", VOCABULARY, names)
    slash_completions("/agent edit s", VOCABULARY, names)
    slash_completions("/memory list --agent ", VOCABULARY, names)
    assert calls == ["read"] * 3


@pytest.mark.parametrize(
    ("text", "items"),
    [
        ("/agent ", ("create", "edit", "delete")),
        ("/agent e", ("edit",)),
        ("/mcp a", ("add", "approve")),
        ("/memory ", ("list", "connect")),
        ("/memory connect ", ("obsidian", "markdown")),
        ("/memory connect o", ("obsidian",)),
    ],
)
def test_a_command_group_completes_its_actions(text, items):
    assert _complete(text) == SlashCompletions(start=text.rindex(" ") + 1, items=items)


@pytest.mark.parametrize(
    ("text", "items"),
    [
        ("/agent create --", ("--card", "--model", "--name")),
        ("/agent create --n", ("--name",)),
        ("/agent create --name x --m", ("--model",)),
        ("/memory connect obsidian --v", ("--vault",)),
        ("/mcp add -", ("--name", "--url")),
    ],
)
def test_a_command_completes_its_options(text, items):
    assert _complete(text) == SlashCompletions(start=text.rindex(" ") + 1, items=items)


@pytest.mark.parametrize(
    ("text", "items"),
    [
        ("/agent edit ", AGENTS),
        ("/agent edit s", ("scout", "scribe")),
        ("/agent edit --card tower o", ("oracle",)),  # the value of --card isn't the positional
        ("/memory list --agent sc", ("scout", "scribe")),
        ("/memory list -a o", ("oracle",)),
    ],
)
def test_an_argument_that_names_an_agent_completes_agent_names(text, items):
    assert _complete(text) == SlashCompletions(start=text.rindex(" ") + 1, items=items)


@pytest.mark.parametrize(
    "text",
    [
        "/agent create x",  # no positional names an agent
        "/agent edit scout x",  # past the agent argument
        "/agent edit --name ",  # the value of an option that takes one
        "/memory list --limit 1",
        "/agent frobnicate ",  # not an action
        "/mcp add --name x",
    ],
)
def test_nothing_else_completes(text):
    assert _complete(text).items == ()
