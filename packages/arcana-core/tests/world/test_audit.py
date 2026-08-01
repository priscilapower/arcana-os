"""Tests for RoutingAuditLog — append-only JSONL, fail-open."""

from uuid import uuid4

from arcana.types import ResolutionLayer, RoutingDecision
from arcana.world.audit import RoutingAuditLog


def _decision(preview: str = "task") -> RoutingDecision:
    return RoutingDecision(
        task_preview=preview,
        resolved_agent_id=uuid4(),
        layer=ResolutionLayer.RULE,
    )


def test_append_writes_one_json_line_per_decision(tmp_path):
    log = RoutingAuditLog(tmp_path / "world" / "routing_audit.jsonl")
    log.append(_decision("first"))
    log.append(_decision("second"))
    lines = (tmp_path / "world" / "routing_audit.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_tail_returns_decisions_oldest_first(tmp_path):
    log = RoutingAuditLog(tmp_path / "audit.jsonl")
    log.append(_decision("one"))
    log.append(_decision("two"))
    log.append(_decision("three"))
    tail = log.tail(n=2)
    assert [d.task_preview for d in tail] == ["two", "three"]


def test_tail_on_missing_file_is_empty(tmp_path):
    assert RoutingAuditLog(tmp_path / "nope.jsonl").tail() == []


def test_appended_decision_round_trips(tmp_path):
    log = RoutingAuditLog(tmp_path / "audit.jsonl")
    original = _decision("roundtrip")
    log.append(original)
    (loaded,) = log.tail()
    assert loaded.id == original.id
    assert loaded.resolved_agent_id == original.resolved_agent_id
    assert loaded.layer == ResolutionLayer.RULE


def test_append_is_fail_open_when_path_is_a_directory(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    log = RoutingAuditLog(audit_path)
    audit_path.mkdir()  # a directory where the file should be → write fails
    log.append(_decision("swallowed"))  # must not raise


def test_tail_skips_corrupt_lines(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    log = RoutingAuditLog(audit_path)
    log.append(_decision("good"))
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write("{ not valid json\n")
    tail = log.tail()
    assert [d.task_preview for d in tail] == ["good"]
