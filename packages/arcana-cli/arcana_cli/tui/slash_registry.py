"""The session's slash commands, generated from the ``arcana`` command tree.

Every ``arcana <group> <action>`` command is also ``/<group> <action>`` inside
the session, and it is one command, not two: the registry walks the Click tree
Typer builds from :data:`arcana_cli.main.app`, joins each leaf to the body its
module registered with :func:`~arcana_cli.command_impl.command_impl`, parses a
slash line with that leaf's own Click parameters, converts the values with
Typer's own convertors, and awaits the body with the session's renderer. So
``/providers add --provider ollama`` reaches ``add_provider`` with exactly the
arguments ``arcana providers add --provider ollama`` does.

What differs is only what the session can't offer:

* Surface-only options (:data:`SURFACE_ONLY`: ``--json`` and the like) are
  hidden, and refused if typed.
* A secret can't go on the line (``--api-key``, a bearer token in
  ``--header``): the line would stay in the transcript, the exit replay and the
  input history. :meth:`SlashRegistry.redacted` spots such a line so the
  session can refuse it without keeping it; left off, the command asks for the
  secret in a hidden prompt.
* A required argument left off is asked for, instead of failing the command.
* ``--help`` shows the command's help in the transcript.

The line is split with :func:`shlex.split` and handed to Click as a list of
words: nothing on it ever reaches a shell. A few commands stay out of the
session on purpose (:data:`NOT_IN_SESSION`); every other leaf has a body, which
a test holds the command tree to.
"""

import inspect
import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

import typer
import typer.main
from typer.core import TyperArgument, TyperCommand, TyperGroup, TyperOption

from arcana_cli.command_impl import AGENT_METAVAR, IMPLS, CommandImpl, SecretTest
from arcana_cli.main import app
from arcana_cli.tui.completion import CommandCompletion, SlashVocabulary, session_vocabulary
from arcana_cli.ui.renderer import Question, Renderer, required

#: Parsed option values, keyed by the command's parameter names, as its body takes them.
Params = Mapping[str, Any]

#: A command in Click's tree: a leaf, or a group (which is a leaf too when it runs on its own).
ClickCommand = TyperCommand | TyperGroup

#: Parameters that only make sense on the command line, by parameter name: the
#: session has no JSON stream to write (``--json``), is its own session
#: (``--session``) and already has its mouse setting (``--no-mouse``).
SURFACE_ONLY = frozenset({"json_", "session_id", "no_mouse"})

#: Leaves of the command tree that have no slash command, and why.
NOT_IN_SESSION: Mapping[str, str] = {
    "chat": "it opens the session you are already in",
    "run": "the session is the conversation: type the prompt, or /switch to the agent first",
    "init": "a session only opens once ~/.arcana exists, so there is nothing left to initialise",
    "soul edit": "it hands the whole terminal to $EDITOR",
}

#: What a redacted line shows in place of a secret.
HIDDEN = "(hidden)"

#: Asks for a command's help in the session.
HELP_OPTION = "--help"


class SlashUsageError(Exception):
    """A slash command's arguments don't parse; the message says why."""


def _missing_parameter(exc: typer.BadParameter) -> TyperArgument | TyperOption | None:
    """The parameter ``exc`` reports missing, or ``None`` when it reports a bad value.

    Typer vendors Click without exporting ``MissingParameter`` (a
    ``BadParameter``), so it is recognised by name.
    """
    param = exc.param
    if type(exc).__name__ == "MissingParameter" and isinstance(param, TyperArgument | TyperOption):
        return param
    return None


def _label(param: TyperArgument | TyperOption) -> str:
    """How a parameter is named to the user: its longest option, or its argument name."""
    if isinstance(param, TyperOption):
        return max(param.opts, key=len)
    return (param.metavar or param.name or "argument").upper()


@dataclass(frozen=True)
class SlashCommand:
    """One generated slash command: ``/<path>``, parsed by ``command`` and run by ``impl``.

    ``convertors`` are Typer's conversions from parsed Click values to what the
    Typer callback (and so the body) receives: a repeatable option's tuple to a
    list (or ``None`` when not given), a choice's string to its enum.
    """

    path: tuple[str, ...]
    command: ClickCommand
    impl: CommandImpl
    convertors: Mapping[str, Callable[[Any], Any]]

    @property
    def name(self) -> str:
        return "/" + " ".join(self.path)

    @property
    def group(self) -> str:
        """The command's first word, without the slash (``providers``, or ``status`` for a top-level one)."""
        return self.path[0]

    @property
    def one_shot(self) -> str:
        """The one-shot command this is, e.g. ``arcana providers add``."""
        return "arcana " + " ".join(self.path)

    @property
    def summary(self) -> str:
        return self.command.get_short_help_str(limit=120)

    @property
    def secrets(self) -> Mapping[str, SecretTest]:
        """Each option (every spelling of it) whose value can be a secret, with the test that says whether it is."""
        found: dict[str, SecretTest] = {}
        for p in self.params():
            test = self.impl.secrets.get(p.name or "")
            if isinstance(p, TyperOption) and test is not None:
                found.update(dict.fromkeys(p.opts, test))
        return found

    def params(self) -> list[TyperArgument | TyperOption]:
        """Every parameter of the command, hidden ones included."""
        return [p for p in self.command.params if isinstance(p, TyperArgument | TyperOption)]

    def visible_params(self) -> list[TyperArgument | TyperOption]:
        """The parameters offered in the session: not surface-only, not a secret."""
        return [p for p in self.params() if p.name not in SURFACE_ONLY and p.name not in self.impl.secrets]

    def usage(self) -> str:
        """``/agent edit AGENT [options]``."""
        positionals = [_label(p) for p in self.visible_params() if isinstance(p, TyperArgument)]
        has_options = any(isinstance(p, TyperOption) for p in self.visible_params())
        return " ".join([self.name, *positionals, *(["[options]"] if has_options else [])])

    def completion(self) -> CommandCompletion:
        options = [p for p in self.visible_params() if isinstance(p, TyperOption)]
        positionals = [p for p in self.visible_params() if isinstance(p, TyperArgument)]
        agent_argument = next((i for i, p in enumerate(positionals) if p.metavar == AGENT_METAVAR), None)
        return CommandCompletion(
            options=tuple(sorted(opt for p in options for opt in p.opts if opt.startswith("--"))),
            takes_value=frozenset(opt for p in options if not p.is_flag for opt in p.opts),
            agent_options=frozenset(opt for p in options if p.metavar == AGENT_METAVAR for opt in p.opts),
            agent_argument=agent_argument,
        )

    async def parse(self, r: Renderer, args: str) -> Params:
        """Parse ``args`` with the command's own parameters, into the values its body takes.

        A required argument left off is asked for through ``r``. Raises
        :class:`SlashUsageError` when the arguments don't parse, or give a
        surface-only option.
        """
        try:
            tokens = shlex.split(args)
        except ValueError as exc:
            raise SlashUsageError(str(exc)) from exc
        answers: dict[str, str] = {}
        while True:
            try:
                # No --help option: Click would write its page straight to the terminal.
                ctx = self.command.make_context(self.name, list(tokens), help_option_names=[], default_map=answers)
                break
            except typer.BadParameter as exc:
                missing = _missing_parameter(exc)
                if missing is None or missing.name is None or missing.name in answers:
                    raise SlashUsageError(exc.format_message()) from exc
                answers[missing.name] = await r.ask(self._question(missing))
            except Exception as exc:  # Click's other usage errors (Typer vendors Click without exporting them)
                format_message = getattr(exc, "format_message", None)
                raise SlashUsageError(format_message() if callable(format_message) else str(exc)) from exc
        if isinstance(self.command, TyperGroup) and (ctx.args or getattr(ctx, "protected_args", None)):
            raise SlashUsageError(f"Got unexpected extra argument(s) ({' '.join(tokens)})")
        for p in self.params():
            if p.name in SURFACE_ONLY and ctx.params.get(p.name):
                raise SlashUsageError(f"{_label(p)} isn't available inside the session.")
        return {
            name: self.convertors[name](value) if name in self.convertors else value
            for name, value in ctx.params.items()
            if name not in SURFACE_ONLY
        }

    def _question(self, param: TyperArgument | TyperOption) -> Question:
        """The question that asks for a required ``param`` left off the line."""
        is_option = isinstance(param, TyperOption)
        return Question(
            param.help or _label(param),
            secret=param.name in self.impl.secrets,
            validator=required,
            flag=_label(param) if is_option else None,
        )

    def asks_for_help(self, args: str) -> bool:
        """Whether ``args`` ask for the command's help (``--help`` as a word of its own, not inside a quoted value)."""
        return HELP_OPTION in _tokens(args)

    async def run(self, r: Renderer, params: Params) -> None:
        """Await the command's body with ``r`` and ``params``."""
        await self.impl.body(r, **params)


@dataclass(frozen=True)
class Resolved:
    """What a slash line names.

    ``command`` is the generated command it runs (``None`` if none) and
    ``args`` the rest of the line. When the line names a command group but no
    action of it (``/agent``, ``/agent frobnicate``), ``group`` is that group's
    name (``/agent``) and ``command`` is ``None``. When it names a command kept
    out of the session, ``excluded`` is that command (``/soul edit``).
    """

    command: SlashCommand | None
    args: str = ""
    group: str | None = None
    excluded: str | None = None

    @property
    def excluded_reason(self) -> str:
        return NOT_IN_SESSION.get(self.excluded.removeprefix("/"), "") if self.excluded else ""


def _tokens(line: str) -> list[str]:
    try:
        return shlex.split(line)
    except ValueError:  # unbalanced quotes: still look for secrets, word by word
        return line.split()


def _option_value(option: str, tokens: list[str], i: int) -> tuple[str, bool] | None:
    """The value ``tokens[i]`` gives ``option``, and whether it is the next token; ``None`` if it doesn't name it.

    A long option takes ``--opt value`` or ``--opt=value``; a short one may be
    glued to its value (``-kVALUE``) or end a cluster (``-yk VALUE``). A missing
    value is ``""``.
    """
    token = tokens[i]
    following = tokens[i + 1] if i + 1 < len(tokens) else None
    if token == option:
        return following or "", following is not None
    if option.startswith("--"):
        return (token.removeprefix(option + "="), False) if token.startswith(option + "=") else None
    if token.startswith("-") and not token.startswith("--") and option[1] in token[1:]:
        glued = token[token.index(option[1], 1) + 1 :]
        if glued:
            return glued, False
        return following or "", following is not None
    return None


class SlashRegistry:
    """Every generated slash command, plus the lookups the session and its input box need."""

    def __init__(self, commands: Iterable[SlashCommand], groups: Mapping[str, ClickCommand]) -> None:
        self.commands: dict[str, SlashCommand] = {c.name: c for c in commands}
        runnable = {name.removeprefix("/") for name in self.commands} | set(groups)
        #: Each command group (``/agent``, ``/memory connect``) with its in-session actions, in the tree's order.
        self.groups: dict[str, tuple[str, ...]] = {
            "/" + name: tuple(a for a in group.commands if f"{name} {a}" in runnable)
            for name, group in groups.items()
            if isinstance(group, TyperGroup)
        }
        self._group_help = {"/" + name: group.get_short_help_str(limit=120) for name, group in groups.items()}

    def group_summary(self, group: str) -> str:
        return self._group_help.get(group, "")

    def resolve(self, line: str) -> Resolved:
        """The generated command ``line`` runs (see :class:`Resolved`)."""
        words = line.strip().split(None, 1)
        if not words:
            return Resolved(None)
        name, rest = words[0], words[1] if len(words) > 1 else ""
        while name in self.groups:
            action, _, after = rest.partition(" ")
            if action in self.groups[name]:
                name, rest = f"{name} {action}", after.strip()
            elif name in self.commands and (not action or action.startswith("-")):
                break  # a group that runs on its own, given options only
            elif f"{name} {action}".removeprefix("/") in NOT_IN_SESSION:
                return Resolved(None, after.strip(), excluded=f"{name} {action}")
            else:
                return Resolved(None, rest, group=name)
        if name.removeprefix("/") in NOT_IN_SESSION:
            return Resolved(None, rest, excluded=name)
        command = self.commands.get(name)
        return Resolved(command, rest.strip()) if command is not None else Resolved(None, rest)

    def _secrets_for(self, command: SlashCommand | None) -> dict[str, SecretTest]:
        """The secret options to hold a line to: ``command``'s own, and every other command's it doesn't have.

        A secret option the command doesn't have (typed on the wrong command, or
        on a line that names none) is still a secret on the line, in every
        spelling: failing closed on an odd word is better than keeping a key.
        """
        own = {opt for p in command.params() for opt in p.opts} if command is not None else set[str]()
        found: dict[str, SecretTest] = {}
        for other in self.commands.values():
            found.update({opt: test for opt, test in other.secrets.items() if opt not in own})
        if command is not None:
            found.update(command.secrets)
        return found

    def redacted(self, line: str) -> str | None:
        """``line`` with every secret option value replaced by :data:`HIDDEN`, or ``None`` when it carries none.

        Only a slash line can carry one. Besides the command's own secret
        options, a line is held to every other command's, so a mistyped action
        or a secret on the wrong command doesn't get through.
        """
        if not line.lstrip().startswith("/"):
            return None
        secrets = self._secrets_for(self.resolve(line).command)
        tokens = _tokens(line)
        shown: list[str] = []
        found = False
        skip_next = False
        for i, token in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue
            for option, is_secret in secrets.items():
                given = _option_value(option, tokens, i)
                if given is None or not is_secret(given[0]):
                    continue
                value, is_next = given
                found = True
                if is_next:
                    shown.extend([token, HIDDEN])
                    skip_next = True
                else:
                    shown.append(token[: len(token) - len(value)] + HIDDEN)
                break
            else:
                shown.append(token)
        return " ".join(shown) if found else None

    def vocabulary(self) -> SlashVocabulary:
        """What the input box completes: the session's own commands and every generated one."""
        session = session_vocabulary()
        generated = [c.name for c in self.commands.values() if len(c.path) == 1]
        first_words = [g for g in self.groups if " " not in g] + generated
        return SlashVocabulary(
            names=(*session.names, *sorted(n for n in dict.fromkeys(first_words) if n not in session.names)),
            actions=self.groups,
            commands={**session.commands, **{c.name: c.completion() for c in self.commands.values()}},
        )


def _leaves(command: ClickCommand, path: tuple[str, ...]) -> Iterable[tuple[tuple[str, ...], ClickCommand]]:
    """Every runnable command under ``command`` with its path: the leaves, and each group that runs on its own."""
    if isinstance(command, TyperGroup):
        if path and command.invoke_without_command and command.callback is not None:
            yield path, command
        for name, sub in command.commands.items():
            if isinstance(sub, TyperCommand | TyperGroup):
                yield from _leaves(sub, (*path, name))
    else:
        yield path, command


def command_tree() -> ClickCommand:
    """The Click tree Typer builds from ``arcana``'s app."""
    root = typer.main.get_command(app)
    assert isinstance(root, TyperGroup)  # ``arcana`` has sub-commands, so Typer builds a group
    return root


def runnable_commands() -> dict[str, ClickCommand]:
    """Every runnable command in the tree, by Typer path (``"providers add"``, ``"cards"``)."""
    return {" ".join(path): command for path, command in _leaves(command_tree(), ())}


def _groups(command: ClickCommand, path: tuple[str, ...]) -> Iterable[tuple[str, ClickCommand]]:
    if isinstance(command, TyperGroup):
        if path:
            yield " ".join(path), command
        for name, sub in command.commands.items():
            if isinstance(sub, TyperCommand | TyperGroup):
                yield from _groups(sub, (*path, name))


def _convertors(command: ClickCommand) -> dict[str, Callable[[Any], Any]]:
    """Typer's conversions for ``command``'s parameters, from the callback it was built from."""
    callback = command.callback
    if callback is None:
        return {}
    _, convertors, _ = typer.main.get_params_convertors_ctx_param_name_from_function(inspect.unwrap(callback))
    return convertors


def build_registry() -> SlashRegistry:
    """Walk the command tree and join every leaf to its registered body.

    A leaf in :data:`NOT_IN_SESSION` is left out, and so is one without a body
    (which a test forbids).
    """
    root = command_tree()
    commands = [
        SlashCommand(path, command, IMPLS[name], _convertors(command))
        for path, command in _leaves(root, ())
        if (name := " ".join(path)) not in NOT_IN_SESSION and name in IMPLS
    ]
    return SlashRegistry(commands, dict(_groups(root, ())))


@cache
def slash_registry() -> SlashRegistry:
    """The session's slash-command registry, built once on first use."""
    return build_registry()
