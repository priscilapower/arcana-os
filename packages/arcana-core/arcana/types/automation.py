"""Automation, Trigger, and Pipeline types."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TriggerType(str, Enum):
    SCHEDULE = "schedule"        # cron expression
    FILE_WATCH = "file_watch"    # path + glob
    MCP_EVENT = "mcp_event"      # source + event type + filter
    WEBHOOK = "webhook"          # incoming HTTP
    MANUAL = "manual"            # user-initiated only


class Trigger(BaseModel):
    type: TriggerType
    config: dict[str, Any] = {}

    # Examples:
    # schedule:  {"cron": "0 8 * * 1-5"}
    # file_watch: {"path": "~/Documents/inbox", "glob": "*.md"}
    # mcp_event: {"source": "gmail", "event": "new_email", "filter": "from:boss"}
    # webhook:   {"path": "/hooks/my-hook", "secret": "..."}


class PipelineStep(BaseModel):
    """One step in a pipeline — runs an agent with a prompt template."""

    agent_id: UUID
    prompt_template: str
    # {{previous_output}} is available in the template
    # {{trigger_data}} contains the raw trigger payload
    condition: str | None = None       # optional Python expression


class AutomationStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"


class Automation(BaseModel):
    """
    A named automation: trigger → pipeline of agent steps.
    Spreads can be saved as Automations — the pipeline IS the spread.
    """

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    trigger: Trigger
    steps: list[PipelineStep]
    parallel: bool = False             # run steps in parallel vs sequential
    status: AutomationStatus = AutomationStatus.ENABLED
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AutomationRun(BaseModel):
    """A single execution of an Automation."""

    id: UUID = Field(default_factory=uuid4)
    automation_id: UUID
    trigger_data: dict[str, Any] = {}
    step_session_ids: list[UUID] = []  # one session per step
    status: str = "running"
    error: str | None = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
