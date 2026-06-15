"""Tests for AuditLog — append, tail, filtering, edge cases."""

import json

import pytest

from arcana.observability.audit import AuditLog
from arcana.observability.events import ModelCallEvent, SessionEvent


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


def test_append_session_event_writes_jsonl(log):
    event = SessionEvent(
        session_id="s1",
        agent_id="a1",
        agent_name="researcher",
        card="the-hermit",
        modifier_cards=[],
        model="ollama/hermes-3",
        input_tokens=100,
        output_tokens=50,
        duration_ms=1200,
        status="completed",
    )
    log.append(event)

    lines = log.path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "session"
    assert record["session_id"] == "s1"
    assert record["agent_name"] == "researcher"
    assert record["input_tokens"] == 100
    assert record["status"] == "completed"


def test_append_model_call_event(log):
    event = ModelCallEvent(
        session_id="s1",
        model="ollama/hermes-3",
        latency_ms=800,
        input_tokens=100,
        output_tokens=50,
        attempt=1,
        success=True,
    )
    log.append(event)

    lines = log.path.read_text().splitlines()
    record = json.loads(lines[0])
    assert record["type"] == "model_call"
    assert record["latency_ms"] == 800
    assert record["success"] is True
    assert record["error"] is None


def test_tail_returns_last_n_events(log):
    for i in range(5):
        log.append(
            SessionEvent(
                session_id=f"s{i}",
                agent_id="a1",
                agent_name="researcher",
                card="the-fool",
                modifier_cards=[],
                model="ollama/hermes-3",
                input_tokens=10,
                output_tokens=5,
                duration_ms=100,
                status="completed",
            )
        )

    events = log.tail(n=3)
    assert len(events) == 3
    assert events[0]["session_id"] == "s2"
    assert events[-1]["session_id"] == "s4"


def test_tail_filters_by_event_type(log):
    log.append(
        SessionEvent(
            session_id="s1",
            agent_id="a1",
            agent_name="x",
            card="the-fool",
            modifier_cards=[],
            model="ollama/hermes-3",
            input_tokens=10,
            output_tokens=5,
            duration_ms=100,
            status="completed",
        )
    )
    log.append(
        ModelCallEvent(
            session_id="s1",
            model="ollama/hermes-3",
            latency_ms=200,
            input_tokens=10,
            output_tokens=5,
            attempt=1,
            success=True,
        )
    )

    session_events = log.tail(event_type="session")
    assert len(session_events) == 1
    assert session_events[0]["type"] == "session"

    model_events = log.tail(event_type="model_call")
    assert len(model_events) == 1
    assert model_events[0]["type"] == "model_call"


def test_tail_empty_log_returns_empty_list(log):
    assert log.tail() == []


def test_tail_nonexistent_file_returns_empty_list(tmp_path):
    log = AuditLog(tmp_path / "nonexistent" / "audit.jsonl")
    # file does not exist yet
    assert log.tail() == []


def test_tail_skips_corrupt_lines(log):
    log.path.parent.mkdir(parents=True, exist_ok=True)
    with open(log.path, "w") as f:
        f.write("not valid json\n")
        f.write(
            json.dumps(
                {
                    "type": "session",
                    "session_id": "s1",
                    "agent_id": "a1",
                    "agent_name": "x",
                    "card": "the-fool",
                    "modifier_cards": [],
                    "model": "ollama/hermes-3",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "duration_ms": 1,
                    "status": "completed",
                    "timestamp": "2026-06-12T00:00:00+00:00",
                    "cost": None,
                }
            )
            + "\n"
        )

    events = log.tail()
    assert len(events) == 1
    assert events[0]["session_id"] == "s1"


def test_append_is_nonfatal_on_io_error(tmp_path):
    # Point log at a directory (not a file) — write will fail, must not raise
    log = AuditLog(tmp_path)  # path is a directory, open() will fail
    event = ModelCallEvent(
        session_id="s1",
        model="m",
        latency_ms=1,
        input_tokens=1,
        output_tokens=1,
        attempt=1,
        success=True,
    )
    log.append(event)  # should not raise


def test_clear_removes_file(log):
    log.append(
        ModelCallEvent(
            session_id="s1",
            model="m",
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            attempt=1,
            success=True,
        )
    )
    assert log.path.exists()
    log.clear()
    assert not log.path.exists()
