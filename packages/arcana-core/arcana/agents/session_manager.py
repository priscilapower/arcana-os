"""SessionManager — session lifecycle and persistence for agents."""

from pathlib import Path
from uuid import UUID

from arcana.types.session import Message, MessageRole, Session, SessionStatus, SessionTrigger


def _default_base() -> Path:
    return Path.home() / ".arcana" / "agents"


class SessionManager:
    """
    Manages agent sessions on disk.

    Sessions are persisted at ~/.arcana/agents/{agent_id}/sessions/{session_id}.json.

    Phase 1a: stateless — no memory extraction on close. Sessions are written to disk
    for audit and future memory wiring. Memory extraction lands in Phase 1b.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or _default_base()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        agent_id: UUID,
        trigger: SessionTrigger = SessionTrigger.USER,
    ) -> Session:
        """Create and return a new running session. Not persisted until close()."""
        return Session(agent_id=agent_id, triggered_by=trigger)

    def append(self, session: Session, role: MessageRole, content: str) -> Message:
        """Add a message to a session and return the new Message."""
        return session.add_message(role, content)

    def close(
        self,
        session: Session,
        status: SessionStatus = SessionStatus.COMPLETED,
    ) -> None:
        """Close the session and persist it to disk."""
        session.close(status)
        self._persist(session)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self, agent_id: UUID, session_id: UUID) -> Session | None:
        """Load a session from disk, or None if not found."""
        path = self._session_path(agent_id, session_id)
        if not path.exists():
            return None
        return Session.model_validate_json(path.read_text())

    def list_sessions(self, agent_id: UUID) -> list[Session]:
        """Return all sessions for an agent, sorted by started_at ascending."""
        sessions_dir = self._base / str(agent_id) / "sessions"
        if not sessions_dir.exists():
            return []
        sessions: list[Session] = []
        for path in sessions_dir.glob("*.json"):
            try:
                sessions.append(Session.model_validate_json(path.read_text()))
            except Exception:
                pass
        return sorted(sessions, key=lambda s: s.started_at)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _persist(self, session: Session) -> None:
        sessions_dir = self._base / str(session.agent_id) / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        path = sessions_dir / f"{session.id}.json"
        path.write_text(session.model_dump_json(indent=2))

    def _session_path(self, agent_id: UUID, session_id: UUID) -> Path:
        return self._base / str(agent_id) / "sessions" / f"{session_id}.json"
