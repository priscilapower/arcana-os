"""Memory types — scope, decay, and quality architecture."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from arcana.types._utils import now_utc

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MemoryType(StrEnum):
    EPISODIC = "episodic"  # what happened — decays fast
    SEMANTIC = "semantic"  # domain knowledge — decays slow
    PROCEDURAL = "procedural"  # how-to patterns — decays very slow
    PREFERENCE = "preference"  # user likes/dislikes — medium decay + reinforcement


class MemoryScope(StrEnum):
    PRIVATE = "private"  # this agent only
    SHARED = "shared"  # named pool, multiple agents opt-in
    GLOBAL = "global"  # The World + all agents (read); World writes


class DecayStrategy(StrEnum):
    EXPONENTIAL = "exponential"  # natural decay — recommended default
    LINEAR = "linear"  # steady reduction over time
    NONE = "none"  # no decay — The World, pinned entries


class ConfidenceSource(StrEnum):
    AGENT = "agent"  # agent wrote this from its own output
    USER_CONFIRMED = "user_confirmed"  # user explicitly confirmed this fact
    INFERRED = "inferred"  # The World inferred this from patterns
    CONSOLIDATED = "consolidated"  # produced by consolidation pass


class RetrievalMode(StrEnum):
    semantic = "semantic"  # only semantic search
    hybrid = "hybrid"  # semantic + BM25 keyword search
    keyword = "keyword"  # BM25 keyword search only (FTS5)


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------


class DecayProfile(BaseModel):
    """
    Defines how a memory type decays over time for a specific agent.
    Card archetypes set defaults; users can override.
    """

    strategy: DecayStrategy = DecayStrategy.EXPONENTIAL
    half_life_days: float = 30.0
    min_importance: float = 0.1  # floor — never decays below this
    refresh_on_access: bool = True  # accessing an entry resets the decay clock
    consolidation_threshold: float = 0.2  # below this → candidate for consolidation


DEFAULT_DECAY_PROFILES: dict[MemoryType, DecayProfile] = {
    MemoryType.EPISODIC: DecayProfile(
        strategy=DecayStrategy.EXPONENTIAL,
        half_life_days=14.0,
        min_importance=0.05,
        refresh_on_access=True,
        consolidation_threshold=0.25,
    ),
    MemoryType.SEMANTIC: DecayProfile(
        strategy=DecayStrategy.EXPONENTIAL,
        half_life_days=180.0,
        min_importance=0.2,
        refresh_on_access=True,
        consolidation_threshold=0.1,
    ),
    MemoryType.PROCEDURAL: DecayProfile(
        strategy=DecayStrategy.EXPONENTIAL,
        half_life_days=365.0,
        min_importance=0.3,
        refresh_on_access=True,
        consolidation_threshold=0.05,
    ),
    MemoryType.PREFERENCE: DecayProfile(
        strategy=DecayStrategy.EXPONENTIAL,
        half_life_days=60.0,
        min_importance=0.15,
        refresh_on_access=True,
        consolidation_threshold=0.2,
    ),
}

WORLD_DECAY_PROFILES: dict[MemoryType, DecayProfile] = {
    t: DecayProfile(strategy=DecayStrategy.NONE, half_life_days=0, min_importance=1.0) for t in MemoryType
}


# ---------------------------------------------------------------------------
# Core entry
# ---------------------------------------------------------------------------


class MemoryEntry(BaseModel):
    """
    A single memory. Scope determines who can access it.
    Confidence guards against context poisoning from hallucinated facts.
    """

    id: UUID = Field(default_factory=uuid4)
    agent_id: UUID
    type: MemoryType
    content: str

    # --- Scope ---
    scope: MemoryScope = MemoryScope.PRIVATE
    pool_name: str | None = None  # set when scope=SHARED

    # --- Importance & decay ---
    importance: float = 0.5  # base value at write time (0.0–1.0)
    pinned: bool = False  # pinned entries never decay or consolidate
    is_consolidated: bool = False  # produced by The World's consolidation pass
    consolidated_from: list[UUID] = []  # original entry ids, if consolidated

    # --- Quality / anti-poisoning ---
    confidence: float = 1.0  # 0.0–1.0; low = potentially hallucinated
    confidence_source: ConfidenceSource = ConfidenceSource.AGENT
    # Conflict state — set by federation on shared writes when near-duplicate found
    has_conflict: bool = False
    conflict_id: UUID | None = None  # links to MemoryConflict record

    # --- Vector ---
    embedding: list[float] | None = None

    # --- Provenance ---
    source_session_id: UUID | None = None
    tags: list[str] = []

    # --- Access tracking (drives decay refresh) ---
    created_at: datetime = Field(default_factory=now_utc)
    last_accessed_at: datetime = Field(default_factory=now_utc)
    access_count: int = 0

    # Promotion flag: >= 0.9 importance → auto-promote to GLOBAL
    @property
    def should_promote_to_global(self) -> bool:
        return self.importance >= 0.9 and self.scope == MemoryScope.PRIVATE

    def bump_access(self) -> None:
        self.access_count += 1
        self.last_accessed_at = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


class ConflictStatus(StrEnum):
    OPEN = "open"  # detected, not yet resolved
    RESOLVED = "resolved"  # The World chose a winner
    DISMISSED = "dismissed"  # user or World decided both are valid / irrelevant


class MemoryConflict(BaseModel):
    """
    Created when two entries in a shared scope contain contradictory information.
    Detected by MemoryFederation on write; resolved by WorldEngine.

    Example:
        Entry A: "The API rate limit is 1000 req/min"  (written by Hermit)
        Entry B: "The API rate limit is 500 req/min"   (written by Magician)
        → MemoryConflict created, both entries flagged with has_conflict=True
        → World surfaces this in briefing: "1 conflict in project-arcana pool"
        → Resolution: World picks higher-confidence entry as canonical
    """

    id: UUID = Field(default_factory=uuid4)
    pool_name: str  # which shared pool the conflict lives in
    entry_a_id: UUID
    entry_b_id: UUID
    similarity_score: float  # how similar the embeddings were
    status: ConflictStatus = ConflictStatus.OPEN
    resolved_entry_id: UUID | None = None  # winner, if status=RESOLVED
    resolution_note: str = ""
    detected_at: datetime = Field(default_factory=now_utc)
    resolved_at: datetime | None = None


# ---------------------------------------------------------------------------
# Whiteboard — ephemeral task workspace
# ---------------------------------------------------------------------------


class WhiteboardStatus(StrEnum):
    ACTIVE = "active"  # automation run / spread in progress
    PROMOTING = "promoting"  # World is deciding what to keep
    ARCHIVED = "archived"  # run ended, entries archived or promoted
    EXPIRED = "expired"  # TTL exceeded, auto-cleaned


class MemoryWhiteboard(BaseModel):
    """
    Short-lived shared workspace for a single automation run or spread activation.

    Distinct from SharedMemoryPool:
      - Pool:       persistent project memory (weeks/months)
      - Whiteboard: ephemeral task memory (hours/days)

    Lifecycle:
      1. Created when an automation run or spread starts
      2. Agents read/write freely during the run
      3. At run end, WorldEngine decides what to promote to the shared pool
      4. Unpromoted entries are archived; whiteboard is marked ARCHIVED
      5. Auto-cleaned by The World after expires_at

    Example — "Research → Draft → Review" spread:
      Hermit writes: research findings, source URLs, key facts
      Empress reads whiteboard, writes: draft sections
      Justice reads whiteboard, writes: critique notes
      World promotes: final draft + key findings to project pool
      World archives: intermediate reasoning, discarded drafts
    """

    id: UUID = Field(default_factory=uuid4)
    name: str  # e.g. "morning-brief-run-2026-05-18"
    automation_run_id: UUID | None = None
    spread_id: UUID | None = None
    participating_agent_ids: list[UUID] = []

    status: WhiteboardStatus = WhiteboardStatus.ACTIVE
    expires_at: datetime  # set by The World at creation
    promote_to_pool: str | None = None  # target SharedMemoryPool name after run

    promoted_entry_ids: list[UUID] = []  # entries that graduated to pool
    archived_entry_ids: list[UUID] = []  # entries that didn't make the cut

    created_at: datetime = Field(default_factory=now_utc)
    closed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Memory profile (per agent)
# ---------------------------------------------------------------------------


class MemoryProfile(BaseModel):
    """Per-agent memory configuration. Card sets defaults; user can override."""

    agent_id: UUID

    decay_profiles: dict[MemoryType, DecayProfile] = Field(default_factory=lambda: dict(DEFAULT_DECAY_PROFILES))

    # Quality thresholds
    min_confidence_to_store: float = 0.3  # entries below this are not persisted
    min_confidence_for_context: float = 0.5  # entries below this excluded from retrieval

    # Consolidation
    consolidation_enabled: bool = True
    consolidation_schedule: str = "0 3 * * 0"  # weekly Sunday 3am
    last_consolidated_at: datetime | None = None

    # Storage
    archive_before_delete: bool = True
    max_entries: int = 50_000
    max_archive_size_mb: int = 500

    cross_agent_readable: bool = False  # shared pool handles this


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class MemoryQuery(BaseModel):
    agent_id: UUID | None = None
    text: str | None = None
    keywords: list[str] = []
    type: MemoryType | None = None
    scope: MemoryScope | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.semantic
    pool_name: str | None = None
    tags: list[str] = []
    time_from: datetime | None = None
    time_to: datetime | None = None
    limit: int = 10
    min_importance: float = 0.0
    min_confidence: float = 0.0  # filter low-confidence entries
    include_archived: bool = False
    include_conflicted: bool = False  # exclude conflicted entries by default


# ---------------------------------------------------------------------------
# Adapter health
# ---------------------------------------------------------------------------


class AdapterCapabilities(BaseModel):
    supports_vector: bool = False
    supports_full_text: bool = False
    supports_tags: bool = True
    supports_time_range: bool = True
    is_writable: bool = True
    is_persistent: bool = True


class AdapterHealth(BaseModel):
    adapter_id: str
    healthy: bool
    message: str = ""


class EmbeddingMeta(BaseModel):
    """The embedding model a database is pinned to.

    Written when the first embedding is stored; one row per database. Locks the
    database to a model so a later, incompatible embedder cannot silently corrupt
    similarity scores against existing vectors. Datetimes persist as ISO-8601
    ``TEXT``, matching ``MemoryEntry``.
    """

    model_name: str
    dimensions: int
    first_used_at: datetime = Field(default_factory=now_utc)
    entry_count: int = 0


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class MemoryMetrics(BaseModel):
    """
    Quality report for a single agent's memory over a time period.
    Generated by WorldEngine; surfaced in morning briefing and Phase 2 UI.

    Key signals:
      - avg_effective_importance_at_retrieval: low = stale memories dominating
      - retrieval_hit_rate: low = memory not helping; context re-explained each time
      - conflict_count: high = agents writing contradictory facts to shared pool
      - entries_below_consolidation_threshold: high = consolidation backlog
      - avg_memory_context_tokens: high = memory bloating context windows
    """

    agent_id: UUID
    agent_name: str
    period_days: int
    computed_at: datetime = Field(default_factory=now_utc)

    # Store health
    total_entries: int = 0
    entries_by_type: dict[str, int] = {}  # MemoryType.value → count
    entries_by_scope: dict[str, int] = {}  # MemoryScope.value → count
    entries_below_consolidation_threshold: int = 0
    pinned_entries: int = 0
    conflict_count: int = 0  # open conflicts in shared pools

    # Retrieval quality
    avg_entries_retrieved_per_session: float = 0.0
    avg_effective_importance_at_retrieval: float = 0.0
    retrieval_hit_rate: float = 0.0  # 0–1: fraction of queries that found results
    avg_confidence_at_retrieval: float = 0.0

    # Cost proxy
    avg_memory_context_tokens: int = 0  # tokens memory adds to context per session
    total_sessions_this_period: int = 0

    # Decay state
    avg_effective_importance: float = 0.0  # across all entries right now
    oldest_active_entry_days: float = 0.0


# ---------------------------------------------------------------------------
# Consolidation report
# ---------------------------------------------------------------------------


class ConsolidationReport(BaseModel):
    """Produced by WorldEngine.consolidate() - surfaced in morning briefing."""

    agent_id: UUID
    agent_name: str
    entries_reviewed: int
    entries_consolidated: int
    entries_archived: int
    new_semantic_memories: int
    conflicts_detected: int = 0
    conflicts_resolved: int = 0
    ran_at: datetime = Field(default_factory=now_utc)


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryAdapter(Protocol):
    """Structural interface for any memory backend wired into an Agent."""

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]: ...
    async def write(self, entry: MemoryEntry) -> None: ...
