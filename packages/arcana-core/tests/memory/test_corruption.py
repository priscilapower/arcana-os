"""Unit tests for SQLiteAdapter corruption handling (ADR-015 T7).

Covers detect-on-open (``PRAGMA quick_check``), detect-on-read error
translation, the quarantine latch, and that healthy stores are unaffected.
"""

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from arcana.memory import MemoryCorruptError, MemoryStorageError, SQLiteAdapter
from arcana.types import MemoryEntry, MemoryQuery, MemoryType

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _entry() -> MemoryEntry:
    return MemoryEntry(agent_id=uuid4(), type=MemoryType.EPISODIC, content="the sky is blue")


async def _make_valid_db(path: Path, *, rows: int) -> None:
    """Create a real multi-page store, then drop WAL sidecars so the main file
    holds every row before a test corrupts it."""
    a = SQLiteAdapter(path)
    await a.connect()
    for _ in range(rows):
        await a.write(_entry())
    await a.aclose()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _smash_pages_after_header(path: Path) -> None:
    """Overwrite every byte past the first page with garbage.

    Page 1 (the header + sqlite_master root) stays intact so the file still
    opens as a database, but the b-tree pages behind it are destroyed — exactly
    what ``quick_check`` exists to catch.
    """
    size = path.stat().st_size
    assert size > 4096, "need a multi-page db to corrupt pages behind the header"
    with open(path, "r+b") as f:
        f.seek(4096)
        f.write(b"\xab" * (size - 4096))


# --------------------------------------------------------------------------
# Healthy path (quick_check must not false-positive)
# --------------------------------------------------------------------------


async def test_healthy_store_passes_quick_check(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "m.db")  # quick_check_on_open=True by default
    await a.connect()
    await a.write(_entry())
    health = await a.health_check()
    assert health.healthy
    await a.aclose()


# --------------------------------------------------------------------------
# Detect-on-open
# --------------------------------------------------------------------------


async def test_garbage_file_detected_on_open(tmp_path: Path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is definitely not a sqlite database" * 8)
    a = SQLiteAdapter(bad)
    with pytest.raises(MemoryCorruptError):
        await a.connect()


async def test_corrupt_pages_detected_by_quick_check(tmp_path: Path):
    p = tmp_path / "m.db"
    await _make_valid_db(p, rows=300)  # ensure several pages
    _smash_pages_after_header(p)
    a = SQLiteAdapter(p)  # quick_check_on_open=True
    with pytest.raises(MemoryCorruptError):
        await a.connect()


# --------------------------------------------------------------------------
# Detect-on-read (quick_check disabled — the shared/global path)
# --------------------------------------------------------------------------


async def test_detect_on_read_surfaces_corruption(tmp_path: Path):
    p = tmp_path / "m.db"
    await _make_valid_db(p, rows=300)
    _smash_pages_after_header(p)
    a = SQLiteAdapter(p, quick_check_on_open=False)
    # Whether the malformed pages surface at connect or when the query reads
    # them, the caller sees MemoryCorruptError out of the read entry point.
    with pytest.raises(MemoryCorruptError):
        await a.search(MemoryQuery(text="sky"))

    # Surfacing corruption must also release the underlying connection. aiosqlite
    # runs each connection on a non-daemon thread; leaving it open leaks that
    # thread and blocks interpreter shutdown (a hung test process, not a failure).
    assert a._conn is None


# --------------------------------------------------------------------------
# Quarantine latch
# --------------------------------------------------------------------------


async def test_quarantine_latches_and_health_reports_it(tmp_path: Path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"garbage" * 64)
    a = SQLiteAdapter(bad)
    with pytest.raises(MemoryCorruptError):
        await a.connect()

    # Latched: health reports corruption without another open attempt...
    health = await a.health_check()
    assert not health.healthy
    assert "corrupt" in health.message.lower()

    # ...and a second connect fails fast rather than reopening the bad file.
    with pytest.raises(MemoryCorruptError):
        await a.connect()


# --------------------------------------------------------------------------
# Error classification (the translation helper, deterministic)
# --------------------------------------------------------------------------


def test_translate_flags_corruption_markers(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "a.db")
    result = a._translate_sqlite_error(sqlite3.OperationalError("database disk image is malformed"), "read failed")
    assert isinstance(result, MemoryCorruptError)
    assert a._corrupt is not None  # latched for the quarantine guard


def test_translate_leaves_ordinary_errors_as_storage_errors(tmp_path: Path):
    a = SQLiteAdapter(tmp_path / "b.db")
    result = a._translate_sqlite_error(sqlite3.OperationalError("no such column: foo"), "search failed")
    assert isinstance(result, MemoryStorageError)
    assert not isinstance(result, MemoryCorruptError)
    assert a._corrupt is None  # a non-corruption error must not quarantine
