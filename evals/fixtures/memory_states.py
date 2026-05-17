"""
Pre-seeded memory states for eval cases.
These are injected into agents before running prompts.
"""

from datetime import datetime, timedelta
from uuid import uuid4

from arcana.types.memory import ConfidenceSource, MemoryEntry, MemoryScope, MemoryType

_AGENT_ID = uuid4()  # placeholder — replaced by harness at runtime


def fresh_work_context() -> list[MemoryEntry]:
    """User's current work context — recent, high confidence."""
    return [
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.SEMANTIC,
            content="User is building a Python RAG system using ChromaDB and LangChain",
            importance=0.8,
            confidence=0.95,
            confidence_source=ConfidenceSource.USER_CONFIRMED,
            scope=MemoryScope.PRIVATE,
        ),
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.PREFERENCE,
            content="User prefers concise answers with code examples over lengthy explanations",
            importance=0.85,
            confidence=1.0,
            confidence_source=ConfidenceSource.USER_CONFIRMED,
            scope=MemoryScope.PRIVATE,
        ),
    ]


def stale_vs_fresh_work() -> list[MemoryEntry]:
    """
    Two contradictory work context entries — one stale, one fresh.
    Used to test that decay correctly ranks fresh over stale.
    """
    one_year_ago = datetime.utcnow() - timedelta(days=365)
    yesterday = datetime.utcnow() - timedelta(days=1)

    return [
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.SEMANTIC,
            content="User works at Google as a senior software engineer",
            importance=0.7,
            confidence=0.9,
            last_accessed_at=one_year_ago,
            created_at=one_year_ago,
            scope=MemoryScope.PRIVATE,
        ),
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.SEMANTIC,
            content="User works at an early-stage AI startup as CTO",
            importance=0.7,
            confidence=0.9,
            last_accessed_at=yesterday,
            created_at=yesterday,
            scope=MemoryScope.PRIVATE,
        ),
    ]


def low_confidence_state() -> list[MemoryEntry]:
    """
    Mix of high and low confidence entries.
    Used to test confidence filtering — low confidence should not surface.
    """
    return [
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.SEMANTIC,
            content="User is probably interested in distributed systems (inferred)",
            importance=0.5,
            confidence=0.25,  # below min_confidence_for_context
            confidence_source=ConfidenceSource.INFERRED,
            scope=MemoryScope.PRIVATE,
        ),
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.PREFERENCE,
            content="User explicitly asked for bullet-point summaries",
            importance=0.8,
            confidence=1.0,
            confidence_source=ConfidenceSource.USER_CONFIRMED,
            scope=MemoryScope.PRIVATE,
        ),
    ]


def rich_project_context() -> list[MemoryEntry]:
    """Full project context for a realistic memory recall test."""
    return [
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.SEMANTIC,
            content="Project: Arcana OS — agentic operating system with tarot card archetypes",
            importance=0.95,
            confidence=1.0,
            confidence_source=ConfidenceSource.USER_CONFIRMED,
            scope=MemoryScope.PRIVATE,
        ),
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.SEMANTIC,
            content="Tech stack: Python, Pydantic, aiosqlite, sqlite-vec, Ollama, MCP SDK",
            importance=0.85,
            confidence=0.95,
            scope=MemoryScope.PRIVATE,
        ),
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.EPISODIC,
            content="Previous session: discussed three-tier memory architecture (private/shared/global)",
            importance=0.7,
            confidence=0.9,
            scope=MemoryScope.PRIVATE,
        ),
        MemoryEntry(
            agent_id=_AGENT_ID,
            type=MemoryType.PREFERENCE,
            content="User wants local-first architecture, prefers SQLite over hosted solutions",
            importance=0.9,
            confidence=1.0,
            confidence_source=ConfidenceSource.USER_CONFIRMED,
            scope=MemoryScope.PRIVATE,
        ),
    ]
