"""Tests for the learning-loop signal sink — append, tail, and fail-open."""

from uuid import uuid4

from arcana.types import QualitySignalSource, SessionQualitySignal
from arcana.world.learning import LearningSignalLog


def _signal(value: float = -0.5) -> SessionQualitySignal:
    return SessionQualitySignal(source=QualitySignalSource.USER_RETRY, value=value, agent_id=uuid4())


def test_emit_then_tail_roundtrips(tmp_path):
    log = LearningSignalLog(tmp_path / "world" / "quality_signals.jsonl")
    a, b = _signal(), _signal(-0.5)
    log.emit(a)
    log.emit(b)
    tailed = log.tail()
    assert [s.id for s in tailed] == [a.id, b.id]  # oldest-first
    assert tailed[0].source == QualitySignalSource.USER_RETRY


def test_tail_empty_when_no_file(tmp_path):
    log = LearningSignalLog(tmp_path / "world" / "quality_signals.jsonl")
    assert log.tail() == []


def test_emit_is_fail_open(tmp_path):
    # A directory where the log file should be makes open(..., "a") raise; emit
    # must swallow it rather than propagate.
    path = tmp_path / "world" / "quality_signals.jsonl"
    log = LearningSignalLog(path)  # ctor creates the parent dir
    path.mkdir()  # now a directory sits where the file should be
    log.emit(_signal())  # must not raise


def test_tail_skips_corrupt_lines(tmp_path):
    path = tmp_path / "world" / "quality_signals.jsonl"
    log = LearningSignalLog(path)
    good = _signal()
    log.emit(good)
    with open(path, "a", encoding="utf-8") as f:
        f.write("not json\n")
    tailed = log.tail()
    assert [s.id for s in tailed] == [good.id]
