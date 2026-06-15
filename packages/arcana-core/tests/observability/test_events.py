"""Tests for event dataclasses — serialization and type field invariants."""

import json

from arcana.observability.events import (
    MemoryReadEvent,
    MemoryWriteEvent,
    ModelCallEvent,
    RoutingEvent,
    SessionEvent,
    event_to_dict,
)


def test_session_event_type_is_always_session():
    e = SessionEvent(
        session_id="s1",
        agent_id="a1",
        agent_name="x",
        card="the-fool",
        modifier_cards=[],
        model="m",
        input_tokens=1,
        output_tokens=1,
        duration_ms=1,
        status="completed",
    )
    assert e.type == "session"


def test_model_call_event_type_is_always_model_call():
    e = ModelCallEvent(
        session_id="s1",
        model="m",
        latency_ms=1,
        input_tokens=1,
        output_tokens=1,
        attempt=1,
        success=True,
    )
    assert e.type == "model_call"


def test_routing_event_type_is_always_routing():
    e = RoutingEvent(
        session_id="s1",
        prompt_preview="hello",
        outcome="matched",
        matched_rule_trigger="when user asks for code",
        target_agent_name="magician",
        target_card="the-magician",
        rules_evaluated=5,
        confidence=0.9,
        duration_ms=30,
    )
    assert e.type == "routing"
    assert e.namespace_id == "local"
    assert e.workspace_id == "default"


def test_memory_read_event():
    e = MemoryReadEvent(
        session_id="s1",
        agent_id="a1",
        query_text="summarize rag",
        results_count=3,
        latency_ms=12,
    )
    assert e.type == "memory_read"


def test_memory_write_event():
    e = MemoryWriteEvent(
        session_id="s1",
        agent_id="a1",
        memory_type="episodic",
        importance=0.5,
    )
    assert e.type == "memory_write"


def test_event_to_dict_is_json_serializable():
    e = SessionEvent(
        session_id="s1",
        agent_id="a1",
        agent_name="x",
        card="the-fool",
        modifier_cards=["the-empress"],
        model="ollama/hermes-3",
        input_tokens=100,
        output_tokens=50,
        duration_ms=1200,
        status="completed",
        cost=0.001,
    )
    d = event_to_dict(e)
    # must be JSON-serializable without errors
    serialized = json.dumps(d)
    restored = json.loads(serialized)
    assert restored["type"] == "session"
    assert restored["cost"] == 0.001
    assert restored["modifier_cards"] == ["the-empress"]


def test_model_call_event_with_error():
    e = ModelCallEvent(
        session_id="s1",
        model="m",
        latency_ms=500,
        input_tokens=0,
        output_tokens=0,
        attempt=2,
        success=False,
        error="503 Service Unavailable",
    )
    d = event_to_dict(e)
    assert d["success"] is False
    assert d["error"] == "503 Service Unavailable"
    assert d["attempt"] == 2


def test_session_event_timestamp_is_iso_string():
    e = SessionEvent(
        session_id="s",
        agent_id="a",
        agent_name="x",
        card="c",
        modifier_cards=[],
        model="m",
        input_tokens=0,
        output_tokens=0,
        duration_ms=0,
        status="completed",
    )
    # Should be parseable ISO 8601 string
    assert "T" in e.timestamp
    assert "+" in e.timestamp or "Z" in e.timestamp
