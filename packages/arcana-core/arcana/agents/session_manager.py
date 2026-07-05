"""SessionManager — session lifecycle and persistence for agents."""

import logging
from pathlib import Path
from uuid import UUID

from arcana.memory.extraction import (
    MemoryExtractor,
    build_consolidated_entry,
    heuristic_summary,
)
from arcana.memory.extraction.signals import ENGLISH
from arcana.types.memory import MemoryAdapter
from arcana.types.session import Message, MessageRole, Session, SessionStatus, SessionTrigger

logger = logging.getLogger("arcana.agents.session_manager")


def _default_base() -> Path:
    return Path.home() / ".arcana" / "agents"


class SessionManager:
    """
    Manages agent sessions on disk.

    Sessions are persisted at ``~/.arcana/agents/{agent_id}/sessions/{session_id}.json``.

    An optional ``extractor`` drives :meth:`summarise`; without one, summaries
    fall back to a deterministic, model-free heuristic.
    """

    def __init__(self, base_dir: Path | None = None, *, extractor: MemoryExtractor | None = None) -> None:
        self._base = base_dir or _default_base()
        self._extractor = extractor

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
        """Close the session and persist it to disk (no summarisation)."""
        session.close(status)
        self._persist(session)

    async def summarise(
        self,
        session: Session,
        *,
        extractor: MemoryExtractor | None = None,
    ) -> str:
        """Distil the session into ``session.summary``, persist, and return it.

        Uses *extractor* (or the manager's own), falling back to a deterministic
        heuristic when neither is set or the extractor raises. Never propagates an
        extractor failure — summarisation is best-effort.
        """
        ext = extractor or self._extractor
        text = ""
        if ext is not None:
            try:
                text = await ext.summarise(session)
            except Exception:
                logger.warning("session summarisation failed; using heuristic", exc_info=True)
        if not text:
            text = heuristic_summary(session.messages)
        session.summary = text
        self._persist(session)
        return text

    async def close_and_summarise(
        self,
        session: Session,
        status: SessionStatus = SessionStatus.COMPLETED,
        *,
        summarise: bool = True,
        memory: MemoryAdapter | None = None,
        extractor: MemoryExtractor | None = None,
    ) -> None:
        """Close the session, optionally summarising and consolidating.

        When ``summarise`` is set, distils ``session.summary`` and — given a
        ``memory`` adapter — writes one consolidated memory carrying it. The whole
        summarise/consolidate step is best-effort: a failure is logged, the
        session is still closed and persisted, and the run is never affected.
        """
        session.close(status)
        if not summarise:
            self._persist(session)
            return

        try:
            ext = extractor or self._extractor
            await self.summarise(session, extractor=ext)
            if memory is not None:
                signals = ext.signals if ext is not None else ENGLISH
                entry = build_consolidated_entry(session, signals=signals)
                if entry is not None:
                    await memory.write(entry)
                    session.memories_extracted.append(entry.id)
                    self._persist(session)
        except Exception:
            logger.warning("session close summarisation/consolidation failed", exc_info=True)
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
