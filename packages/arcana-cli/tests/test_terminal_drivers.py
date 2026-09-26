"""Textual is the CLI's only terminal driver.

No other terminal toolkit is a dependency or imported, no module reads raw keys or runs a
``rich.live.Live`` display, only the line-prompt adapter behind the renderer port calls
``typer.prompt`` / ``typer.confirm``, and no command writes to the terminal itself — its output
goes through the renderer port."""

import ast
import importlib.metadata
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

import arcana_cli

SOURCE = Path(arcana_cli.__file__).parent
LOCKFILE = SOURCE.parents[2] / "uv.lock"

#: Modules the CLI no longer drives the terminal with: a raw-key reader, the old chat
#: editor's toolkit, and Rich's live display.
BANNED = ("readchar", "prompt_toolkit", "rich.live")

#: Distributions ``arcana-cli`` must not pull in, directly or through a dependency
#: (normalised names, as the lockfile spells them).
BANNED_DISTRIBUTIONS = frozenset({"readchar", "prompt-toolkit"})


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ── dependencies: one terminal toolkit ───────────────────────────────────


def test_arcana_cli_does_not_depend_on_another_terminal_toolkit():
    requirements = importlib.metadata.requires("arcana-cli") or []
    declared = {_normalise(re.split(r"[\s;<>=!~\[(]", req, maxsplit=1)[0]) for req in requirements}
    assert "textual" in declared
    assert declared & BANNED_DISTRIBUTIONS == set()


def _locked_closure(lock: dict[str, Any], root: str) -> set[str]:
    """Every package ``root`` pulls in according to the lockfile, extras included, dev groups left out."""
    by_name = {pkg["name"]: pkg for pkg in lock["package"]}
    seen: set[str] = set()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        pkg = by_name[name]
        todo.extend(dep["name"] for dep in pkg.get("dependencies", []))
        for extra in pkg.get("optional-dependencies", {}).values():
            todo.extend(dep["name"] for dep in extra)
    return seen


@pytest.mark.skipif(not LOCKFILE.exists(), reason="needs the workspace lockfile")
def test_nothing_arcana_cli_installs_pulls_in_another_terminal_toolkit():
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    closure = _locked_closure(lock, "arcana-cli")
    assert {"arcana-core", "textual"} <= closure
    assert closure & BANNED_DISTRIBUTIONS == set()


def test_the_lockfile_scan_follows_transitive_dependencies():
    lock = {
        "package": [
            {"name": "arcana-cli", "dependencies": [{"name": "a"}]},
            {"name": "a", "optional-dependencies": {"x": [{"name": "readchar"}]}},
            {"name": "readchar"},
        ]
    }
    assert _locked_closure(lock, "arcana-cli") == {"arcana-cli", "a", "readchar"}


# ── imports: no other terminal driver ────────────────────────────────────


def _imported_modules(path: Path) -> set[str]:
    """Every module ``path`` imports, with each of its parent packages, however the import is spelled.

    ``from pkg import name`` also counts ``pkg.name``, since ``name`` may itself be a module.
    """
    found: set[str] = set()

    def add(dotted: str) -> None:
        parts = dotted.split(".")
        found.update(".".join(parts[: i + 1]) for i in range(len(parts)))

    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            add(node.module)
            for alias in node.names:
                add(f"{node.module}.{alias.name}")
    return found


def test_the_scan_sees_the_source_tree():
    assert len(list(SOURCE.rglob("*.py"))) > 20


@pytest.mark.parametrize("module", BANNED)
def test_no_module_imports_a_banned_terminal_driver(module: str):
    offenders = sorted(
        str(path.relative_to(SOURCE)) for path in SOURCE.rglob("*.py") if module in _imported_modules(path)
    )
    assert offenders == [], f"{module} is imported by {offenders}"


@pytest.mark.parametrize(
    ("module", "spelling"),
    [
        ("readchar", "import readchar"),
        ("readchar", "from readchar import key"),
        ("readchar", "import readchar.key as k"),
        ("prompt_toolkit", "from prompt_toolkit import PromptSession"),
        ("prompt_toolkit", "from prompt_toolkit.history import FileHistory"),
        ("prompt_toolkit", "import prompt_toolkit.application as a"),
        ("rich.live", "from rich.live import Live"),
        ("rich.live", "import rich.live"),
        ("rich.live", "from rich import live"),
    ],
)
def test_the_scan_catches_an_import(tmp_path: Path, module: str, spelling: str):
    probe = tmp_path / "probe.py"
    probe.write_text(f"def f():\n    {spelling}\n")
    assert module in _imported_modules(probe)


def test_the_scan_leaves_the_rest_of_rich_alone(tmp_path: Path):
    probe = tmp_path / "probe.py"
    probe.write_text("from rich.console import Console\nimport rich.text\n")
    assert "rich.live" not in _imported_modules(probe)


# ── line prompts: one door ────────────────────────────────────────────────

#: Line-prompt functions a command must not call itself: questions go through the renderer port.
LINE_PROMPTS = frozenset({"prompt", "confirm"})
PROMPT_MODULES = frozenset({"typer", "click"})
#: The one module allowed to call them: the line-prompt adapter behind the port.
PROMPT_ADAPTER = Path("ui") / "renderer" / "tty.py"


def _line_prompt_uses(path: Path) -> list[str]:
    """Each ``typer.prompt`` / ``click.confirm`` … reference in ``path``, however it is imported."""
    uses: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in LINE_PROMPTS
            and isinstance(node.value, ast.Name)
            and node.value.id in PROMPT_MODULES
        ):
            uses.append(f"{node.value.id}.{node.attr}")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            root = node.module.split(".")[0]
            if root in PROMPT_MODULES or node.module.startswith("typer._click"):
                uses.extend(f"{node.module}.{a.name}" for a in node.names if a.name in LINE_PROMPTS)
    return uses


def test_only_the_line_prompt_adapter_calls_typer_prompt_or_confirm():
    offenders = {
        str(path.relative_to(SOURCE)): uses
        for path in SOURCE.rglob("*.py")
        if path.relative_to(SOURCE) != PROMPT_ADAPTER and (uses := _line_prompt_uses(path))
    }
    assert offenders == {}, f"ask through the Renderer instead: {offenders}"
    assert _line_prompt_uses(SOURCE / PROMPT_ADAPTER)  # the adapter itself still does


@pytest.mark.parametrize(
    "spelling",
    ["typer.prompt('Name')", "typer.confirm('Sure?', abort=True)", "click.prompt('x')"],
)
def test_the_prompt_scan_catches_a_call(tmp_path: Path, spelling: str):
    probe = tmp_path / "probe.py"
    probe.write_text(f"import typer\nimport click\n\ndef f():\n    {spelling}\n")
    assert _line_prompt_uses(probe)


def test_the_prompt_scan_catches_a_from_import(tmp_path: Path):
    probe = tmp_path / "probe.py"
    probe.write_text("from typer import confirm\n")
    assert _line_prompt_uses(probe) == ["typer.confirm"]


# ── output: one door ──────────────────────────────────────────────────────

#: Where command code lives: every command body and what it calls to sign in.
COMMAND_CODE = [
    *sorted((SOURCE / "commands").rglob("*.py")),
    SOURCE / "_oauth.py",
    SOURCE / "main.py",
]
#: Functions that write to the terminal (or the ``--json`` stream) directly, bypassing the renderer.
DIRECT_WRITERS = frozenset({"print", "emit_json", "emit_error", "echo", "secho"})
#: The one command module allowed a direct write: after the session app has released the terminal,
#: ``run_chat`` replays the transcript and prints the resume hint to the replay console (ADR-024 A3).
EXIT_REPLAY = Path("commands") / "chat" / "app.py"


def _direct_output(path: Path) -> list[str]:
    """Each ``Console(...)`` construction and direct write (``print``, ``x.print``, ``emit_json`` …) in ``path``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name == "Console":
            found.append(f"line {node.lineno}: Console(...)")
        elif name in DIRECT_WRITERS:
            found.append(f"line {node.lineno}: {ast.unparse(func)}(...)")
    return found


def test_the_output_scan_covers_every_command_module():
    assert len(COMMAND_CODE) > 12
    assert all(path.exists() for path in COMMAND_CODE)


def test_no_command_writes_to_the_terminal_itself():
    offenders = {
        str(path.relative_to(SOURCE)): found
        for path in COMMAND_CODE
        if path.relative_to(SOURCE) != EXIT_REPLAY and (found := _direct_output(path))
    }
    assert offenders == {}, f"emit / note / fail through the Renderer instead: {offenders}"


def test_the_exit_replay_is_the_only_direct_write_in_the_chat_app():
    assert [f.split(": ", 1)[1] for f in _direct_output(SOURCE / EXIT_REPLAY)] == ["out.print(...)"]


@pytest.mark.parametrize(
    "spelling",
    [
        "console = Console()",
        "Console(stderr=True).print('x')",
        "rich.console.Console()",
        "console.print('x')",
        "print('x')",
        "emit_json({})",
        "_render.emit_error(1, 'x')",
        "typer.echo('x')",
    ],
)
def test_the_output_scan_catches_a_direct_write(tmp_path: Path, spelling: str):
    probe = tmp_path / "probe.py"
    probe.write_text(f"def f():\n    {spelling}\n")
    assert _direct_output(probe)


def test_the_output_scan_leaves_the_renderer_alone(tmp_path: Path):
    probe = tmp_path / "probe.py"
    probe.write_text("def f(r):\n    r.emit('x')\n    r.note('y')\n    fail(r, 'z')\n")
    assert _direct_output(probe) == []
