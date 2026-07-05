"""Tests for SessionManager."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from arcana.agents.session_manager import SessionManager
from arcana.types.memory import MemoryEntry, MemoryType
from arcana.types.session import MessageRole, SessionStatus, SessionTrigger


def test_start_returns_running_session(tmp_session_manager: SessionManager):
    agent_id = uuid4()
    session = tmp_session_manager.start(agent_id)
    assert session.agent_id == agent_id
    assert session.status == SessionStatus.RUNNING


def test_start_sets_trigger(tmp_session_manager: SessionManager):
    agent_id = uuid4()
    session = tmp_session_manager.start(agent_id, SessionTrigger.WORLD)
    assert session.triggered_by == SessionTrigger.WORLD


def test_start_default_trigger_is_user(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    assert session.triggered_by == SessionTrigger.USER


def test_append_adds_message(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    msg = tmp_session_manager.append(session, MessageRole.USER, "Hello!")
    assert msg.content == "Hello!"
    assert msg.role == MessageRole.USER
    assert len(session.messages) == 1


def test_append_multiple_messages(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.append(session, MessageRole.USER, "Q")
    tmp_session_manager.append(session, MessageRole.ASSISTANT, "A")
    assert len(session.messages) == 2


def test_close_sets_completed_status(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.close(session)
    assert session.status == SessionStatus.COMPLETED
    assert session.ended_at is not None


def test_close_accepts_custom_status(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.close(session, SessionStatus.FAILED)
    assert session.status == SessionStatus.FAILED


def test_close_persists_session_to_disk(tmp_session_manager: SessionManager, tmp_path):
    agent_id = uuid4()
    session = tmp_session_manager.start(agent_id)
    tmp_session_manager.close(session)

    session_file = tmp_path / "agents" / str(agent_id) / "sessions" / f"{session.id}.json"
    assert session_file.exists()


def test_load_returns_persisted_session(tmp_session_manager: SessionManager):
    agent_id = uuid4()
    session = tmp_session_manager.start(agent_id)
    tmp_session_manager.append(session, MessageRole.USER, "remember me")
    tmp_session_manager.close(session)

    loaded = tmp_session_manager.load(agent_id, session.id)
    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.messages[0].content == "remember me"
    assert loaded.status == SessionStatus.COMPLETED


def test_load_returns_none_for_unknown_session(tmp_session_manager: SessionManager):
    result = tmp_session_manager.load(uuid4(), uuid4())
    assert result is None


def test_list_sessions_returns_all_sessions(tmp_session_manager: SessionManager):
    agent_id = uuid4()
    for _ in range(3):
        s = tmp_session_manager.start(agent_id)
        tmp_session_manager.close(s)

    sessions = tmp_session_manager.list_sessions(agent_id)
    assert len(sessions) == 3


def test_list_sessions_returns_empty_for_unknown_agent(tmp_session_manager: SessionManager):
    assert tmp_session_manager.list_sessions(uuid4()) == []


def test_list_sessions_sorted_by_started_at(tmp_session_manager: SessionManager):
    agent_id = uuid4()
    for _ in range(3):
        s = tmp_session_manager.start(agent_id)
        tmp_session_manager.close(s)

    sessions = tmp_session_manager.list_sessions(agent_id)
    times = [s.started_at for s in sessions]
    assert times == sorted(times)


def test_list_sessions_scoped_to_agent(tmp_session_manager: SessionManager):
    agent_a = uuid4()
    agent_b = uuid4()

    for _ in range(2):
        s = tmp_session_manager.start(agent_a)
        tmp_session_manager.close(s)

    s = tmp_session_manager.start(agent_b)
    tmp_session_manager.close(s)

    assert len(tmp_session_manager.list_sessions(agent_a)) == 2
    assert len(tmp_session_manager.list_sessions(agent_b)) == 1


def test_session_records_duration_ms(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.close(session)
    assert session.duration_ms >= 0


# ---------------------------------------------------------------------------
# summarise() + close_and_summarise()
# ---------------------------------------------------------------------------


def _extractor(summary: str) -> MagicMock:
    ext = MagicMock()
    ext.summarise = AsyncMock(return_value=summary)
    return ext


async def test_summarise_uses_heuristic_without_extractor(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.append(session, MessageRole.USER, "how do I bake bread?")
    tmp_session_manager.append(session, MessageRole.ASSISTANT, "Mix, proof, and bake.")

    summary = await tmp_session_manager.summarise(session)

    assert session.summary == summary
    assert "how do I bake bread?" in summary
    assert "Mix, proof, and bake." in summary


async def test_summarise_persists_summary_to_disk(tmp_session_manager: SessionManager):
    agent_id = uuid4()
    session = tmp_session_manager.start(agent_id)
    tmp_session_manager.append(session, MessageRole.USER, "remember me")

    await tmp_session_manager.summarise(session)

    loaded = tmp_session_manager.load(agent_id, session.id)
    assert loaded is not None
    assert loaded.summary == session.summary


async def test_summarise_prefers_injected_extractor(tmp_path):
    sm = SessionManager(base_dir=tmp_path / "agents", extractor=_extractor("distilled summary"))
    session = sm.start(uuid4())
    sm.append(session, MessageRole.USER, "hello")

    summary = await sm.summarise(session)

    assert summary == "distilled summary"


async def test_close_and_summarise_writes_one_consolidated_memory(tmp_session_manager: SessionManager):
    memory = MagicMock()
    memory.write = AsyncMock()
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.append(session, MessageRole.USER, "Remember that I prefer metric units")
    tmp_session_manager.append(session, MessageRole.ASSISTANT, "Understood, metric it is.")

    await tmp_session_manager.close_and_summarise(session, memory=memory)

    assert session.status == SessionStatus.COMPLETED
    memory.write.assert_awaited_once()
    written: MemoryEntry = memory.write.call_args[0][0]
    # A durable user preference consolidates as SEMANTIC.
    assert written.type == MemoryType.SEMANTIC
    assert written.content == session.summary
    assert written.id in session.memories_extracted


async def test_close_and_summarise_disabled_skips_summary_and_write(tmp_session_manager: SessionManager):
    memory = MagicMock()
    memory.write = AsyncMock()
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.append(session, MessageRole.USER, "hi")

    await tmp_session_manager.close_and_summarise(session, summarise=False, memory=memory)

    assert session.status == SessionStatus.COMPLETED
    assert session.summary is None
    memory.write.assert_not_awaited()


async def test_close_and_summarise_without_memory_still_summarises(tmp_session_manager: SessionManager):
    session = tmp_session_manager.start(uuid4())
    tmp_session_manager.append(session, MessageRole.USER, "what is the capital of France?")
    tmp_session_manager.append(session, MessageRole.ASSISTANT, "Paris.")

    await tmp_session_manager.close_and_summarise(session, memory=None)

    assert session.summary is not None
    assert session.status == SessionStatus.COMPLETED
