"""Textual is the CLI's only terminal driver: no module reads raw keys or runs a ``rich.live.Live`` display,
and only the line-prompt adapter behind the renderer port calls ``typer.prompt`` / ``typer.confirm``."""

import ast
from pathlib import Path

import pytest

import arcana_cli

SOURCE = Path(arcana_cli.__file__).parent

#: Modules the CLI no longer drives the terminal with: a raw-key reader and Rich's live display.
BANNED = ("readchar", "rich.live")


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
