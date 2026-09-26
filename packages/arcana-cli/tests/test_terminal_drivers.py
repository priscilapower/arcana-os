"""Textual is the CLI's only raw-key terminal driver: no module reads keys any other way."""

import ast
from pathlib import Path

import pytest

import arcana_cli

SOURCE = Path(arcana_cli.__file__).parent

#: Raw-key readers the CLI no longer drives the terminal with.
BANNED = ("readchar",)


def _imported_modules(path: Path) -> set[str]:
    """The top-level package of every module ``path`` imports, however the import is spelled."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_the_scan_sees_the_source_tree():
    assert len(list(SOURCE.rglob("*.py"))) > 20


@pytest.mark.parametrize("module", BANNED)
def test_no_module_imports_a_banned_key_reader(module: str):
    offenders = sorted(
        str(path.relative_to(SOURCE)) for path in SOURCE.rglob("*.py") if module in _imported_modules(path)
    )
    assert offenders == [], f"{module} is imported by {offenders}"


def test_the_scan_catches_an_import(tmp_path: Path):
    for spelling in ("import readchar", "from readchar import key", "import readchar.key as k"):
        probe = tmp_path / "probe.py"
        probe.write_text(f"def f():\n    {spelling}\n")
        assert "readchar" in _imported_modules(probe)
