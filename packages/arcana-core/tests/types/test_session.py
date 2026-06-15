from uuid import UUID, uuid4

from arcana.types import (
    Message,
    MessageRole,
    Session,
    SessionStatus,
    SessionTrigger,
    ToolCall,
)


def test_message_defaults():
    msg = Message(role=MessageRole.USER, content="Hello")
    assert isinstance(msg.id, UUID)
    assert msg.role == MessageRole.USER
    assert msg.content == "Hello"
    assert msg.timestamp is not None


def test_message_role_values():
    assert MessageRole.USER == "user"
    assert MessageRole.ASSISTANT == "assistant"
    assert MessageRole.TOOL == "tool"
    assert MessageRole.SYSTEM == "system"


def test_tool_call_defaults():
    tc = ToolCall(tool_name="web_search", params={"query": "arcana"})
    assert isinstance(tc.id, UUID)
    assert tc.result is None
    assert tc.error is None
    assert tc.duration_ms == 0


def test_tool_call_with_result():
    tc = ToolCall(
        tool_name="web_search",
        params={"query": "arcana"},
        result={"hits": 10},
        duration_ms=120,
    )
    assert tc.result == {"hits": 10}
    assert tc.duration_ms == 120


def test_tool_call_with_error():
    tc = ToolCall(tool_name="broken_tool", params={}, error="Timeout", duration_ms=5000)
    assert tc.error == "Timeout"


def test_session_status_values():
    assert SessionStatus.RUNNING == "running"
    assert SessionStatus.COMPLETED == "completed"
    assert SessionStatus.FAILED == "failed"
    assert SessionStatus.INTERRUPTED == "interrupted"


def test_session_trigger_values():
    assert SessionTrigger.USER == "user"
    assert SessionTrigger.WORLD == "world"
    assert SessionTrigger.AGENT == "agent"
    assert SessionTrigger.SCHEDULE == "schedule"
    assert SessionTrigger.AUTOMATION == "automation"


def test_session_defaults():
    session = _make_session()
    assert session.status == SessionStatus.RUNNING
    assert session.triggered_by == SessionTrigger.USER
    assert session.messages == []
    assert session.tool_calls == []
    assert session.total_input_tokens == 0
    assert session.total_output_tokens == 0
    assert session.total_cost is None
    assert session.duration_ms == 0
    assert session.summary is None
    assert session.memories_extracted == []
    assert session.ended_at is None
    assert session.automation_id is None
    assert isinstance(session.id, UUID)


def test_session_add_message_appends_and_returns():
    session = _make_session()
    msg = session.add_message(MessageRole.USER, "What is RAG?")
    assert len(session.messages) == 1
    assert session.messages[0] is msg
    assert msg.role == MessageRole.USER
    assert msg.content == "What is RAG?"


def test_session_add_multiple_messages():
    session = _make_session()
    session.add_message(MessageRole.USER, "Hello")
    session.add_message(MessageRole.ASSISTANT, "Hi there")
    assert len(session.messages) == 2
    assert session.messages[1].role == MessageRole.ASSISTANT


def test_session_close_sets_status_and_time():
    session = _make_session()
    session.close(SessionStatus.COMPLETED)
    assert session.status == SessionStatus.COMPLETED
    assert session.ended_at is not None


def test_session_close_computes_duration():
    session = _make_session()
    session.close()
    assert session.duration_ms >= 0


def test_session_close_default_status_is_completed():
    session = _make_session()
    session.close()
    assert session.status == SessionStatus.COMPLETED


def test_session_close_failed():
    session = _make_session()
    session.close(SessionStatus.FAILED)
    assert session.status == SessionStatus.FAILED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(**kwargs) -> Session:
    defaults = dict(agent_id=uuid4())
    return Session(**{**defaults, **kwargs})
