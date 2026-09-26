"""The slash-command registry generated from the ``arcana`` command tree.

The drift tests are the point: a new Typer command fails CI until it is either
reachable in the session (a body registered with ``@command_impl``) or listed in
``NOT_IN_SESSION`` with a reason; a body must take exactly its Typer callback's
parameters, which the callback passes straight through; and a new option that
looks like it could carry a secret fails until it is declared secret (so the
session keeps it off the line) or listed as not one, with a reason.
"""

import ast
import inspect
import re
import textwrap
from collections.abc import Callable
from typing import Any, get_type_hints

import pytest
from typer.core import TyperGroup, TyperOption

from arcana.types import MemoryScope, MemoryType
from arcana_cli.command_impl import AGENT_METAVAR, IMPLS, CommandGroup
from arcana_cli.tui.slash_registry import (
    NOT_IN_SESSION,
    SURFACE_ONLY,
    ClickCommand,
    SlashUsageError,
    command_tree,
    runnable_commands,
    slash_registry,
)
from arcana_cli.ui.input_model import SESSION_COMMANDS, SessionCommandName
from tests.support.renderer import RecordingRenderer

RUNNABLE = runnable_commands()
REGISTRY = slash_registry()


# ── drift: every command has a decision ───────────────────────────────────


def test_the_tree_has_the_commands_it_is_known_to_have():
    assert {"providers add", "mcp approve", "memory connect obsidian", "cards", "status"} <= set(RUNNABLE)


def test_every_command_is_in_the_session_or_kept_out_with_a_reason():
    undecided = sorted(set(RUNNABLE) - set(IMPLS) - set(NOT_IN_SESSION))
    assert undecided == [], f"register a body with @command_impl, or add to NOT_IN_SESSION with a reason: {undecided}"


def test_no_command_is_both_in_the_session_and_kept_out():
    assert set(IMPLS) & set(NOT_IN_SESSION) == set()


def test_every_registered_body_and_exclusion_names_a_real_command():
    assert set(IMPLS) - set(RUNNABLE) == set()
    assert set(NOT_IN_SESSION) - set(RUNNABLE) == set()
    assert all(reason.strip() for reason in NOT_IN_SESSION.values())


def test_the_registry_holds_every_registered_command():
    assert {c.name for c in REGISTRY.commands.values()} == {"/" + path for path in IMPLS}


def test_every_top_level_group_is_a_command_group():
    root = command_tree()
    assert isinstance(root, TyperGroup)
    groups = {name for name, sub in root.commands.items() if isinstance(sub, TyperGroup)}
    assert groups == {g.value for g in CommandGroup}


def test_no_generated_command_takes_a_session_commands_name():
    session = {c.name.value for c in SESSION_COMMANDS}
    assert session & set(REGISTRY.commands) == set()


def test_every_session_command_is_declared_once():
    assert [c.name for c in SESSION_COMMANDS] == list(SessionCommandName)


# ── one command: the body takes what the callback passes, unchanged ────────


def _callback(command: ClickCommand) -> Callable[..., Any]:
    assert command.callback is not None
    return inspect.unwrap(command.callback)


def _callback_params(command: ClickCommand) -> dict[str, Any]:
    """The Typer callback's parameters and their types, minus the surface-only ones and the Typer context."""
    hints = get_type_hints(_callback(command))
    return {
        name: hints[name]
        for name in inspect.signature(_callback(command)).parameters
        if name not in SURFACE_ONLY and "Context" not in str(hints[name])
    }


def _body_params(path: str) -> dict[str, Any]:
    body = IMPLS[path].body
    hints = get_type_hints(body)
    names = list(inspect.signature(body).parameters)[1:]  # after the renderer
    return {name: hints[name] for name in names}


@pytest.mark.parametrize("path", sorted(IMPLS))
def test_a_body_takes_exactly_its_callbacks_parameters(path):
    assert _body_params(path) == _callback_params(RUNNABLE[path])


#: Callbacks that do more than hand their parameters to the body, and why that is still one command.
NOT_PASS_THROUGH = {
    "cards": "--json has no picker to browse with, so it emits the catalog instead; the session has no --json",
}


def _body_call(callback: Callable[..., Any]) -> ast.Call:
    """The ``body(renderer_for(json_), …)`` call inside the callback's ``run_async(…)``."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(callback)))
    runs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_async"
    ]
    assert len(runs) == 1, "the callback runs its body exactly once"
    call = runs[0].args[0]
    assert isinstance(call, ast.Call)
    return call


@pytest.mark.parametrize("path", sorted(set(IMPLS) - set(NOT_PASS_THROUGH)))
def test_a_callback_passes_its_parameters_straight_to_its_body(path):
    callback = _callback(RUNNABLE[path])
    call = _body_call(callback)
    assert isinstance(call.func, ast.Name) and call.func.id == IMPLS[path].body.__name__
    renderer, *positional = call.args
    assert ast.unparse(renderer) in ("renderer_for(json_)", "renderer_for(json=False)")
    assert all(isinstance(a, ast.Name) for a in positional), ast.unparse(call)
    assert all(isinstance(k.value, ast.Name) and k.value.id == k.arg for k in call.keywords), ast.unparse(call)
    passed = [a.id for a in positional if isinstance(a, ast.Name)] + [k.arg for k in call.keywords if k.arg]
    assert sorted(passed) == sorted(_callback_params(RUNNABLE[path]))


def test_the_pass_through_scan_catches_a_converted_value():
    def callback(json_: bool, scope: list[str] | None) -> None:
        run_async(body(renderer_for(json_), scope=list(scope or [])))  # noqa: F821  # pyright: ignore

    call = _body_call(callback)
    assert not all(isinstance(k.value, ast.Name) and k.value.id == k.arg for k in call.keywords)


# ── secrets: every option that could carry one is decided ─────────────────

#: A parameter name that suggests its value could be a secret.
SECRETISH = re.compile(r"key|token|secret|pass|header|auth|cred|bearer|cookie", re.IGNORECASE)

#: Secret-looking options whose values aren't secrets, and why.
NOT_SECRET = {
    ("providers add", "api_key_env"): "names an environment variable; the key is read from it, never typed",
    ("providers edit", "api_key_env"): "names an environment variable; the key is read from it, never typed",
    ("mcp add", "auth_key"): "names an existing keyring entry, not the token in it",
    ("providers edit", "header"): "custom-adapter headers are plain configuration, stored in models.json and "
    "shown by 'providers show'",
}


def _valued_options(command: ClickCommand) -> list[TyperOption]:
    return [p for p in command.params if isinstance(p, TyperOption) and not p.is_flag]


@pytest.mark.parametrize("path", sorted(RUNNABLE))
def test_every_secret_looking_option_is_declared_secret_or_not(path):
    impl = IMPLS.get(path)
    declared = set(impl.secrets) if impl is not None else set()
    undecided = [
        p.name
        for p in _valued_options(RUNNABLE[path])
        if p.name and SECRETISH.search(p.name) and p.name not in declared and (path, p.name) not in NOT_SECRET
    ]
    assert undecided == [], (
        f"'{path}': declare {undecided} secret in @command_impl(secrets=…), or add it to NOT_SECRET with a reason"
    )


def test_every_declared_secret_is_a_valued_option():
    for path, impl in IMPLS.items():
        valued = {p.name for p in _valued_options(RUNNABLE[path])}
        assert set(impl.secrets) <= valued, path
    assert set(NOT_SECRET) <= {(path, p.name) for path in RUNNABLE for p in _valued_options(RUNNABLE[path])}


def test_the_known_secrets_are_declared():
    assert set(REGISTRY.commands["/providers add"].secrets) == {"--api-key", "-k"}
    assert set(REGISTRY.commands["/mcp add"].secrets) == {"--header"}


def test_secret_and_surface_only_options_are_hidden_from_completion_and_help():
    for command in REGISTRY.commands.values():
        offered = set(command.completion().options)
        hidden = {
            opt for p in command.params() if p.name in SURFACE_ONLY or p.name in command.impl.secrets for opt in p.opts
        }
        assert offered & hidden == set(), command.name
        assert all(p.name not in SURFACE_ONLY for p in command.visible_params())
    assert "--json" not in REGISTRY.commands["/agent list"].completion().options


# ── the MCP approval gate ─────────────────────────────────────────────────


def test_only_mcp_approve_admits_a_server_into_the_sessions_tools():
    admitting = [c.name for c in REGISTRY.commands.values() if c.impl.admits_server]
    assert admitting == ["/mcp approve"]
    assert "name" in {p.name for p in REGISTRY.commands["/mcp approve"].params()}


# ── resolving a line ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "name", "args"),
    [
        ("/providers add --provider ollama", "/providers add", "--provider ollama"),
        ("/providers   add   -y", "/providers add", "-y"),
        ("/memory connect obsidian --vault v", "/memory connect obsidian", "--vault v"),
        ("/cards", "/cards", ""),
        ("/cards show hermit", "/cards show", "hermit"),
        ("/status", "/status", ""),
    ],
)
def test_a_line_resolves_to_its_command(line, name, args):
    found = REGISTRY.resolve(line)
    assert found.command is not None and found.command.name == name
    assert found.args == args


@pytest.mark.parametrize(
    ("line", "group"),
    [
        ("/agent", "/agent"),
        ("/agent frobnicate", "/agent"),
        ("/memory connect", "/memory connect"),
        ("/cards x", "/cards"),
    ],
)
def test_a_group_without_an_action_resolves_to_the_group(line, group):
    found = REGISTRY.resolve(line)
    assert found.command is None and found.group == group


@pytest.mark.parametrize(("line", "excluded"), [("/soul edit", "/soul edit"), ("/run hi", "/run"), ("/chat", "/chat")])
def test_a_command_kept_out_resolves_with_its_reason(line, excluded):
    found = REGISTRY.resolve(line)
    assert found.command is None and found.excluded == excluded and found.excluded_reason


def test_the_groups_list_only_the_actions_the_session_has():
    assert REGISTRY.groups["/soul"] == ("show",)
    assert "connect" in REGISTRY.groups["/memory"]


# ── parsing is Click's, and never a shell's ───────────────────────────────


async def test_shell_syntax_is_just_words():
    found = REGISTRY.resolve("/world route 'hi; rm -rf ~' --agent scout")
    assert found.command is not None
    params = await found.command.parse(RecordingRenderer(), found.args)
    assert params["prompt"] == "hi; rm -rf ~"
    show = REGISTRY.commands["/agent show"]
    with pytest.raises(SlashUsageError):  # "rm" and "-rf" are just more words, and not ones it takes
        await show.parse(RecordingRenderer(), "x; rm -rf ~")


async def test_values_convert_as_the_one_shot_command_converts_them():
    params = await REGISTRY.commands["/memory list"].parse(
        RecordingRenderer(), "--scope global --type episodic --limit 5"
    )
    assert params["scope"] is MemoryScope.GLOBAL and params["type_"] is MemoryType.EPISODIC and params["limit"] == 5


# ── completion vocabulary ─────────────────────────────────────────────────


def test_the_vocabulary_covers_every_group_and_command():
    vocabulary = REGISTRY.vocabulary()
    assert vocabulary.names[: len(SESSION_COMMANDS)] == tuple(c.name.value for c in SESSION_COMMANDS)
    assert {"/agent", "/providers", "/mcp", "/memory", "/status", "/cards"} <= set(vocabulary.names)
    assert "/run" not in vocabulary.names and "/chat" not in vocabulary.names
    assert set(REGISTRY.commands) <= set(vocabulary.commands)
    assert len(vocabulary.names) == len(set(vocabulary.names))


def test_agent_arguments_complete_agent_names():
    assert REGISTRY.commands["/agent edit"].completion().agent_argument == 0
    assert REGISTRY.commands["/tools subscribe"].completion().agent_argument == 0
    assert {"--agent", "-a"} <= REGISTRY.commands["/memory list"].completion().agent_options
    assert REGISTRY.commands["/cards show"].completion().agent_argument is None
    for command in REGISTRY.commands.values():
        for p in command.params():
            if p.name == "agent":
                assert p.metavar == AGENT_METAVAR, command.name


def test_help_is_asked_for_by_the_word_not_a_quoted_value():
    route = REGISTRY.commands["/world route"]
    assert route.asks_for_help("--help")
    assert route.asks_for_help("'a prompt' --help")
    assert not route.asks_for_help("'what does --help do'")
