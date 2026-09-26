"""Learning-loop signals — feedback the World records about a routing outcome.

A :class:`SessionQualitySignal` is a small, signed judgement about how well a
session went, tagged with its :class:`QualitySignalSource`. The World emits one
when it observes a low-cost proxy for a bad route — today, a user re-issuing the
same prompt right after the last run (``USER_RETRY``). Signals are written
fire-and-forget through a sink; nothing downstream blocks a route.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc


class QualitySignalSource(StrEnum):
    """Why a :class:`SessionQualitySignal` fired.

    ``USER_RETRY`` — the user re-issued the immediately prior prompt (verbatim,
    same agent) within the retry window, a cheap proxy for "that route missed".
    """

    USER_RETRY = "user_retry"


class SessionQualitySignal(BaseModel):
    """A single quality judgement about a session, fed to the learning loop.

    ``value`` is a signed score (negative = worse); ``USER_RETRY`` carries
    ``-0.5``. The signal names the ``agent_id`` it concerns and keeps a short
    ``task_preview`` for inspection — never the full task text.
    """

    id: UUID = Field(default_factory=uuid4)
    source: QualitySignalSource
    value: float
    agent_id: UUID
    task_preview: str = ""
    created_at: datetime = Field(default_factory=now_utc)

    namespace_id: str = "local"
