"""Tests for the ADR-005 filesystem guardrails. No LLM."""

import sys
from pathlib import Path

import pytest

from arcana.memory import paths
from arcana.memory.errors import PathSafetyError


@pytest.fixture
def scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Confine the guardrails to ``tmp_path`` for the test (default root is $HOME)."""
    monkeypatch.setattr(paths, "_GUARDRAILS", paths.MemoryGuardrails(scope_paths=str(tmp_path)))
    return tmp_path


# --------------------------------------------------------------------------
# Confinement — the path_traversal Finding class
# --------------------------------------------------------------------------


def test_existing_dir_inside_root_is_allowed(scope: Path):
    vault = scope / "vault"
    vault.mkdir()
    assert paths.resolve_existing_dir(str(vault)) == vault.resolve()


def test_dotdot_escape_is_rejected(scope: Path):
    with pytest.raises(PathSafetyError):
        paths.resolve_existing_dir(str(scope / ".." / ".." / "etc"))


def test_absolute_jump_outside_root_is_rejected(scope: Path):
    with pytest.raises(PathSafetyError):
        paths.resolve_existing_dir("/etc")


def test_symlink_escaping_root_is_rejected(scope: Path):
    outside = scope.parent / "outside_vault"
    outside.mkdir()
    link = scope / "link"
    link.symlink_to(outside, target_is_directory=True)
    # The link lives inside the root, but realpath resolves it to `outside`.
    with pytest.raises(PathSafetyError):
        paths.resolve_existing_dir(str(link))


def test_missing_dir_is_rejected(scope: Path):
    with pytest.raises(PathSafetyError):
        paths.resolve_existing_dir(str(scope / "nope"))


def test_file_where_dir_expected_is_rejected(scope: Path):
    f = scope / "file.md"
    f.write_text("x")
    with pytest.raises(PathSafetyError):
        paths.resolve_existing_dir(str(f))


def test_out_path_parent_must_exist(scope: Path):
    with pytest.raises(PathSafetyError):
        paths.resolve_out_path(str(scope / "missing_dir" / "out.md"))


def test_out_path_rejects_directory_target(scope: Path):
    d = scope / "adir"
    d.mkdir()
    with pytest.raises(PathSafetyError):
        paths.resolve_out_path(str(d))


# --------------------------------------------------------------------------
# atomic_write_text — clobber guard, size cap, atomicity
# --------------------------------------------------------------------------


def test_atomic_write_creates_file(scope: Path):
    out = paths.resolve_out_path(str(scope / "dump.md"))
    paths.atomic_write_text(out, "hello", overwrite=False)
    assert out.read_text() == "hello"


def test_atomic_write_refuses_to_clobber_without_overwrite(scope: Path):
    out = paths.resolve_out_path(str(scope / "dump.md"))
    paths.atomic_write_text(out, "first", overwrite=False)
    with pytest.raises(PathSafetyError):
        paths.atomic_write_text(out, "second", overwrite=False)
    assert out.read_text() == "first"  # original untouched


def test_atomic_write_overwrites_when_allowed(scope: Path):
    out = paths.resolve_out_path(str(scope / "dump.md"))
    paths.atomic_write_text(out, "first", overwrite=False)
    paths.atomic_write_text(out, "second", overwrite=True)
    assert out.read_text() == "second"


def test_atomic_write_enforces_size_cap(scope: Path):
    out = paths.resolve_out_path(str(scope / "dump.md"))
    with pytest.raises(PathSafetyError):
        paths.atomic_write_text(out, "x" * 100, overwrite=False, max_bytes=10)
    assert not out.exists()  # nothing written on a rejected cap


def test_atomic_write_leaves_no_temp_files(scope: Path):
    out = paths.resolve_out_path(str(scope / "dump.md"))
    paths.atomic_write_text(out, "content", overwrite=False)
    siblings = [p.name for p in scope.iterdir()]
    assert siblings == ["dump.md"]  # temp file was renamed away, not left behind


# --------------------------------------------------------------------------
# Scope configuration
# --------------------------------------------------------------------------


def test_default_root_is_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths, "_GUARDRAILS", paths.MemoryGuardrails(scope_paths=""))
    assert paths.allowed_roots() == [Path.home().resolve()]


def test_multiple_scope_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    joined = f"{a}{__import__('os').pathsep}{b}"
    monkeypatch.setattr(paths, "_GUARDRAILS", paths.MemoryGuardrails(scope_paths=joined))
    roots = paths.allowed_roots()
    assert a.resolve() in roots and b.resolve() in roots


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_symlink_note(scope: Path):
    # Sanity: a symlink pointing *inside* the root resolves and is allowed.
    real = scope / "real"
    real.mkdir()
    link = scope / "alias"
    link.symlink_to(real, target_is_directory=True)
    assert paths.resolve_existing_dir(str(link)) == real.resolve()
