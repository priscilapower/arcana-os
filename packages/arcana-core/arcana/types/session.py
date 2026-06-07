"""Session, Message, and ToolCall types."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class Message(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=now_utc)


class ToolCall(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tool_name: str
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0


class SessionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class SessionTrigger(StrEnum):
    USER = "user"
    WORLD = "world"
    AGENT = "agent"
    SCHEDULE = "schedule"
    AUTOMATION = "automation"


class Session(BaseModel):
    """A single conversation or task run with an agent."""

    id: UUID = Field(default_factory=uuid4)
    agent_id: UUID
    triggered_by: SessionTrigger = SessionTrigger.USER
    automation_id: UUID | None = None  # set if triggered by automation

    messages: list[Message] = []
    tool_calls: list[ToolCall] = []

    # Observability
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float | None = None
    duration_ms: int = 0

    status: SessionStatus = SessionStatus.RUNNING
    summary: str | None = None
    memories_extracted: list[UUID] = []

    started_at: datetime = Field(default_factory=now_utc)
    ended_at: datetime | None = None

    def add_message(self, role: MessageRole, content: str) -> Message:
        msg = Message(role=role, content=content)
        self.messages.append(msg)
        return msg

    def close(self, status: SessionStatus = SessionStatus.COMPLETED) -> None:
        self.status = status
        self.ended_at = datetime.now(tz=UTC)
        if self.started_at:
            self.duration_ms = int((self.ended_at - self.started_at).total_seconds() * 1000)
