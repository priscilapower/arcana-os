"""The docs' slash-command tables, generated from the slash-command registry.

``docs/chat.md`` and ``docs/cli.md`` include these tables from
``docs/snippets/``; each is generated here from the same registry the session
runs (the ``arcana`` command tree plus the session's own commands), and the
test fails when a committed table no longer matches it: a new command, a
renamed action, a changed summary, a command gaining or losing ``--json``.

Set ``ARCANA_RECORD_GOLDENS=1`` to rewrite the snippets instead of comparing.
"""

import os
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from arcana_cli.tui.slash_registry import NOT_IN_SESSION, SlashRegistry, runnable_commands, slash_registry
from arcana_cli.ui.input_model import SESSION_COMMANDS

SNIPPETS = Path(__file__).resolve().parents[3] / "docs" / "snippets"
RECORD = os.environ.get("ARCANA_RECORD_GOLDENS") == "1"

#: How to regenerate the snippets.
REGENERATE = "ARCANA_RECORD_GOLDENS=1 uv run pytest packages/arcana-cli/tests/test_docs_reference.py"

HEADER = (
    "<!-- Generated from the slash-command registry by packages/arcana-cli/tests/test_docs_reference.py.\n"
    f"     Don't edit by hand: run `{REGENERATE}` to regenerate. -->\n"
)


def _cell(text: str) -> str:
    """``text`` made safe for a Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def _code(text: str) -> str:
    """``text`` as inline code in a table cell. MkDocs' table parser doesn't split on a ``|`` inside
    backticks (and would show an escaping ``\\``), so pipes stay as they are."""
    return f"`{' '.join(text.split())}`"


def _table(headings: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    heads = list(headings)
    lines = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return HEADER + "\n".join(lines) + "\n"


def session_commands_table(_registry: SlashRegistry) -> str:
    """The commands only the session has."""
    return _table(["Command", "What it does"], ([_code(c.usage), _cell(c.help)] for c in SESSION_COMMANDS))


def _actions(group: str, actions: Iterable[str], registry: SlashRegistry) -> str:
    """``create|list``; bracketed when the group also runs on its own (``/cards [show]``)."""
    listed = "|".join(actions)
    return f"[{listed}]" if group in registry.commands else listed


def command_groups_table(registry: SlashRegistry) -> str:
    """Every ``arcana`` command group and top-level command as the session offers it, one row each (as ``/help``)."""
    rows = [
        [_code(f"{group} {_actions(group, actions, registry)}"), _cell(registry.group_summary(group))]
        for group, actions in registry.groups.items()
        if " " not in group and actions
    ]
    rows += [
        [_code(command.usage()), _cell(command.summary)]
        for command in registry.commands.values()
        if len(command.path) == 1 and command.name not in registry.groups
    ]
    return _table(["Command", "What it does"], rows)


def one_shot_table(registry: SlashRegistry) -> str:
    """Every runnable ``arcana`` command: its slash command (or why it has none) and whether it takes ``--json``."""
    rows: list[list[str]] = []
    for path, command in runnable_commands().items():
        slash = registry.commands.get("/" + path)
        in_session = _code(slash.name) if slash is not None else f"— {_cell(NOT_IN_SESSION[path])}"
        takes_json = any(p.name == "json_" for p in command.params)
        rows.append(
            [
                _code(f"arcana {path}"),
                in_session,
                "yes" if takes_json else "—",
                _cell(command.get_short_help_str(limit=200)),
            ]
        )
    return _table(["Command", "In the session", "`--json`", "What it does"], rows)


TABLES: dict[str, Callable[[SlashRegistry], str]] = {
    "slash-session-commands.md": session_commands_table,
    "slash-command-groups.md": command_groups_table,
    "cli-commands.md": one_shot_table,
}


@pytest.mark.skipif(not SNIPPETS.parent.is_dir(), reason="needs the repository's docs/")
@pytest.mark.parametrize("filename", sorted(TABLES))
def test_the_docs_table_matches_the_registry(filename: str):
    expected = TABLES[filename](slash_registry())
    path = SNIPPETS / filename
    if RECORD:
        SNIPPETS.mkdir(exist_ok=True)
        path.write_text(expected, encoding="utf-8")
    assert path.exists(), f"{path} is missing: run `{REGENERATE}`"
    assert path.read_text(encoding="utf-8") == expected, (
        f"docs/snippets/{filename} is stale: run `{REGENERATE}` and commit the result"
    )


@pytest.mark.skipif(not SNIPPETS.parent.is_dir(), reason="needs the repository's docs/")
@pytest.mark.parametrize(
    ("page", "filename"),
    [("chat.md", f) for f in sorted(TABLES) if f.startswith("slash")] + [("cli.md", "cli-commands.md")],
)
def test_the_docs_page_includes_its_table(page: str, filename: str):
    assert f'--8<-- "docs/snippets/{filename}"' in (SNIPPETS.parent / page).read_text(encoding="utf-8")
