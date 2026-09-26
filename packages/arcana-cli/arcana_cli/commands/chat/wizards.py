"""The setup wizards reachable from inside the session: ``/agent``, ``/providers`` and ``/mcp``.

Each slash command runs the very coroutine its one-shot command runs
(``/providers add`` awaits :func:`~arcana_cli.commands.providers.add_provider`),
handed the session's renderer, so it asks the same questions and writes the
same files and keyring entries. Its arguments are parsed by the one-shot
command's own options: ``/providers add --provider ollama`` means exactly what
``arcana providers add --provider ollama`` means.

Two things differ, both for the session's sake:

* ``--json`` is refused: the session has no JSON stream to write to.
* A secret can't go on the command line (``--api-key``, a bearer token in
  ``--header``): the line would be kept in the transcript, the exit replay and
  the input history. Left off, the wizard asks for it in a hidden prompt, whose
  answer reaches the keyring and nothing else. :func:`redacted` spots such a
  line so the session can refuse it without keeping it.
"""

import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import typer
import typer.main
from pydantic import TypeAdapter

from arcana_cli.commands import agent, mcp, providers
from arcana_cli.ui.input_model import WizardGroup
from arcana_cli.ui.renderer import Renderer

#: Parsed option values, keyed by the one-shot command's parameter names.
Params = Mapping[str, Any]

#: Whether an option's value is a secret, given the value as typed.
SecretTest = Callable[[str], bool]

#: What a redacted line shows in place of a secret.
HIDDEN = "(hidden)"

#: The one-shot option that has no in-session equivalent.
JSON_OPTION = "--json"

_TEXT: TypeAdapter[str | None] = TypeAdapter(str | None)
_REQUIRED_TEXT = TypeAdapter(str)
_TEXTS = TypeAdapter(list[str])
_FLAG = TypeAdapter(bool)


class WizardUsageError(Exception):
    """A slash command's arguments don't parse; the message says why."""


def _text(p: Params, key: str) -> str | None:
    return _TEXT.validate_python(p.get(key))


def _required(p: Params, key: str) -> str:
    return _REQUIRED_TEXT.validate_python(p.get(key))


def _texts(p: Params, key: str) -> list[str]:
    """A repeatable option's values; an option that wasn't given parses as no values."""
    return _TEXTS.validate_python(p.get(key) or [])


def _texts_or_none(p: Params, key: str) -> list[str] | None:
    """A repeatable option's values, or ``None`` when it wasn't given (as the one-shot command sees it)."""
    return _texts(p, key) or None


def _flag(p: Params, key: str) -> bool:
    return _FLAG.validate_python(p.get(key, False))


def _any_value(_value: str) -> bool:
    return True


def _bearer_value(value: str) -> bool:
    """Whether an ``Authorization=Bearer <token>`` header carries a token (a blank one is asked for instead)."""
    token = value.partition("=")[2].strip()
    if token.lower().startswith("bearer"):
        token = token[len("bearer") :].strip()
    return bool(token)


@dataclass(frozen=True)
class Wizard:
    """One in-session command: ``command action``, parsed by ``options`` and run by ``run``.

    ``options`` is the one-shot command's Typer callback; only its parameters are
    used, to parse the arguments. ``secrets`` maps each option whose value is a
    secret to the test that says whether a given value is one. ``admits_server``
    marks the wizard that approves an MCP server (``--name``/the argument ``name``)
    into the running session's tools.
    """

    command: WizardGroup
    action: str
    options: Callable[..., None]
    run: Callable[[Renderer, Params], Awaitable[None]]
    secrets: Mapping[str, SecretTest] = field(default_factory=dict[str, SecretTest])
    admits_server: bool = False

    @property
    def name(self) -> str:
        return f"{self.command} {self.action}"

    @property
    def one_shot(self) -> str:
        """The one-shot command this runs, e.g. ``arcana providers add``."""
        return f"arcana {self.command.removeprefix('/')} {self.action}"

    def parse(self, args: str) -> Params:
        """Parse ``args`` with the one-shot command's options; raises :class:`WizardUsageError` if they don't."""
        try:
            tokens = shlex.split(args)
        except ValueError as exc:
            raise WizardUsageError(str(exc)) from exc
        app = typer.Typer()
        app.command()(self.options)
        command = typer.main.get_command(app)
        try:
            # No --help option: its page would be written straight to the terminal.
            params = command.make_context(self.name, tokens, help_option_names=[]).params
        except Exception as exc:  # click's usage errors (typer vendors click without exporting them)
            format_message = getattr(exc, "format_message", None)
            raise WizardUsageError(format_message() if callable(format_message) else str(exc)) from exc
        if params.get("json_"):
            raise WizardUsageError(f"{JSON_OPTION} isn't available inside the session.")
        return params


def _wizards() -> dict[tuple[WizardGroup, str], Wizard]:
    table = [
        Wizard(
            WizardGroup.AGENT,
            "create",
            agent.create,
            lambda r, p: agent.create_agent(r, name=_text(p, "name"), card=_text(p, "card"), model=_text(p, "model")),
        ),
        Wizard(
            WizardGroup.AGENT,
            "edit",
            agent.edit,
            lambda r, p: agent.edit_agent(
                r,
                _required(p, "name"),
                new_name=_text(p, "new_name"),
                description=_text(p, "description"),
                card=_text(p, "card"),
                model=_text(p, "model"),
                tags=_text(p, "tags"),
            ),
        ),
        Wizard(
            WizardGroup.AGENT,
            "delete",
            agent.delete,
            lambda r, p: agent.delete_agent(r, _required(p, "name"), yes=_flag(p, "yes")),
        ),
        Wizard(
            WizardGroup.PROVIDERS,
            "add",
            providers.add_cmd,
            lambda r, p: providers.add_provider(
                r,
                provider=_text(p, "provider"),
                model_id=_text(p, "model_id"),
                name=_text(p, "name"),
                endpoint=_text(p, "endpoint"),
                api_key=_text(p, "api_key"),
                api_key_env=_text(p, "api_key_env"),
                oauth=_flag(p, "oauth"),
                issuer=_text(p, "issuer"),
                scope=_texts(p, "scope"),
                device=_flag(p, "device"),
                yes=_flag(p, "yes"),
            ),
            secrets={"--api-key": _any_value, "-k": _any_value},
        ),
        Wizard(
            WizardGroup.PROVIDERS,
            "edit",
            providers.edit_cmd,
            lambda r, p: providers.edit_provider(
                r,
                _required(p, "name"),
                base_url=_text(p, "base_url"),
                rotate_key=_flag(p, "rotate_key"),
                api_key_env=_text(p, "api_key_env"),
                header=_texts_or_none(p, "header"),
                no_verify=_flag(p, "no_verify"),
            ),
        ),
        Wizard(
            WizardGroup.PROVIDERS,
            "remove",
            providers.remove_cmd,
            lambda r, p: providers.remove_provider(
                r, _required(p, "name"), yes=_flag(p, "yes"), force=_flag(p, "force")
            ),
        ),
        Wizard(
            WizardGroup.PROVIDERS,
            "login",
            providers.login_cmd,
            lambda r, p: providers.login_provider(r, _required(p, "name"), device=_flag(p, "device")),
        ),
        Wizard(
            WizardGroup.MCP,
            "add",
            mcp.add_cmd,
            lambda r, p: mcp.add_server(
                r,
                name=_required(p, "name"),
                url=_text(p, "url"),
                command=_text(p, "command"),
                args=_texts(p, "arg"),
                transport=_text(p, "transport"),
                header=_texts(p, "header"),
                auth_key=_text(p, "auth_key"),
                oauth=_flag(p, "oauth"),
                issuer=_text(p, "issuer"),
                scope=_texts(p, "scope"),
                device=_flag(p, "device"),
                description=_text(p, "description") or "",
                json_=False,
            ),
            secrets={"--header": _bearer_value},
        ),
        Wizard(
            WizardGroup.MCP,
            "approve",
            mcp.approve_cmd,
            lambda r, p: mcp.approve_server(
                r, _required(p, "name"), tool=_texts(p, "tool"), all_=_flag(p, "all_"), json_=False
            ),
            admits_server=True,
        ),
        Wizard(
            WizardGroup.MCP,
            "remove",
            mcp.remove_cmd,
            lambda r, p: mcp.remove_server(
                r, _required(p, "name"), yes=_flag(p, "yes"), force=_flag(p, "force"), json_=False
            ),
        ),
    ]
    return {(w.command, w.action): w for w in table}


#: Every in-session wizard, keyed by ``(command, action)``.
WIZARDS: dict[tuple[WizardGroup, str], Wizard] = _wizards()


def find_wizard(line: str) -> tuple[Wizard | None, str]:
    """The wizard a ``/command action …`` line names (``None`` if none), and the arguments after the action."""
    command, _, rest = line.strip().partition(" ")
    action, _, args = rest.strip().partition(" ")
    group = next((g for g in WizardGroup if g == command), None)
    return (WIZARDS.get((group, action)) if group is not None else None), args.strip()


def _tokens(args: str) -> list[str]:
    try:
        return shlex.split(args)
    except ValueError:  # unbalanced quotes: still look for secrets, word by word
        return args.split()


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


def redacted(line: str) -> str | None:
    """``line`` with every secret option value replaced by :data:`HIDDEN`, or ``None`` when it carries none.

    Only a wizard line can carry a secret; any other line is ``None``.
    """
    wizard, args = find_wizard(line)
    if wizard is None or not wizard.secrets:
        return None
    tokens = _tokens(args)
    shown: list[str] = []
    found = False
    skip_next = False
    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        for option, is_secret in wizard.secrets.items():
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
    if not found:
        return None
    return " ".join([wizard.name, *shown])
