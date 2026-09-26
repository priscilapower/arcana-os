"""Textual is the CLI's only terminal driver: no module reads raw keys or runs a ``rich.live.Live`` display."""

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
