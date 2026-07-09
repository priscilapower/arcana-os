"""Shared builders for common domain objects used across the test suite.

One definition of each object's default shape, so a change to ``MemoryEntry`` or
``Session`` touches one place instead of the dozen ad-hoc builders that used to
live in individual test modules. Every field is overridable via keyword.
"""

from typing import Any
from uuid import UUID, uuid4

from arcana.types import MemoryEntry, MemoryType
from arcana.types.session import MessageRole, Session


def make_entry(**overrides: Any) -> MemoryEntry:
    """A ``MemoryEntry`` with sensible defaults; override any field by keyword."""
    base: dict[str, Any] = {
        "agent_id": uuid4(),
        "type": MemoryType.SEMANTIC,
        "content": "alpha",
        "importance": 0.5,
    }
    base.update(overrides)
    return MemoryEntry(**base)


def make_session(*turns: tuple[MessageRole, str], agent_id: UUID | None = None) -> Session:
    """A ``Session`` seeded with ``(role, content)`` turns for a (new) agent."""
    session = Session(agent_id=agent_id or uuid4())
    for role, content in turns:
        session.add_message(role, content)
    return session
