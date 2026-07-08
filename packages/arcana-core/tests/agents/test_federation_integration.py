"""Agent ↔ federation integration tests — the full read/write/recall vertical.

Where ``test_memory_integration.py`` drives an ``Agent`` against a ``MagicMock``
adapter (asserting *calls*), and ``tests/memory/`` exercises the federation stack
in isolation, this suite runs a **real** ``Agent`` through a **real** assembled
federation (``build_federation`` → SQLite, optionally a vector GLOBAL tier) and
asserts end-to-end behaviour across sessions:

* cross-session recall — a fact written in one session is retrieved in the next;
* the two-sided confidence contract — sub-threshold entries are neither stored
  nor injected into the prompt;
* resilience — a degraded GLOBAL tier does not fail the run, while a failed
  PRIVATE write surfaces as ``MemoryWriteError``;
* prune that spares pinned / high-importance entries;
* summary consolidation on session close.

Everything is deterministic and fast — no network, no live model, no live
embedder, no sleeps. Failure and time are injected through behavioural seams
(``FailingAdapter``, seeded timestamps), and the private-tier tests run without
any optional extra; only the vector GLOBAL-tier test is ``skipif``-guarded.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from arcana.memory import MemoryWriteError, SQLiteAdapter
from arcana.types import (
    ConfidenceSource,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    PruneMode,
    PrunePolicy,
)
from arcana.types.session import Session
from tests.agents._federation import MakeFederatedAgent
from tests.memory._fakes import FailingAdapter, FakeEmbedding, StubExtractor

# The vector GLOBAL tier needs the optional ``vector`` extra (sqlite-vec). Its one
# test skips without it; every private-tier test below runs regardless.
_HAS_VECTOR = importlib.util.find_spec("sqlite_vec") is not None


def _entry(agent_id: UUID | None, content: str, **overrides: Any) -> MemoryEntry:
    base: dict[str, Any] = dict(
        agent_id=agent_id or uuid4(),
        type=MemoryType.SEMANTIC,
        content=content,
        importance=0.5,
        confidence=0.9,
        confidence_source=ConfidenceSource.USER_CONFIRMED,
        scope=MemoryScope.PRIVATE,
    )
    base.update(overrides)
    return MemoryEntry(**base)


# ---------------------------------------------------------------------------
# cross-session recall
# ---------------------------------------------------------------------------


async def test_cross_session_recall(make_federated_agent: MakeFederatedAgent):
    """A fact stated in session A is recalled into the prompt in session B."""
    fa = await make_federated_agent()

    # Session A: state a durable fact. Extraction persists it to the private store.
    await fa.agent.run("Remember that my project is called Arcana")

    on_disk = fa.home / "agents" / str(fa.agent_id) / "memory.db"
    assert on_disk.exists(), "the private store should be persisted beside the agent"
    stored = await fa.federation.search(MemoryQuery(include_archived=True, limit=50))
    assert stored, "session A should have persisted at least one memory"

    # Session B: a brand-new session for the same agent recalls it.
    await fa.agent.run("What is the name of my project?")

    system = fa.gateway.complete.call_args[0][1].system
    assert "Relevant Memory" in system
    assert "Arcana" in system


# ---------------------------------------------------------------------------
# store confidence filter (entries < 0.3 are never persisted)
# ---------------------------------------------------------------------------


async def test_store_confidence_filter_drops_subthreshold(make_federated_agent: MakeFederatedAgent):
    """An extracted entry below ``min_confidence_to_store`` (0.3) is never written."""
    low = _entry(
        None,
        "UNRELIABLE hallucinated fact",
        confidence=0.2,
        confidence_source=ConfidenceSource.AGENT,
    )
    fa = await make_federated_agent(extractor=StubExtractor([low]), summarise_on_close=False)

    await fa.agent.run("tell me something")

    stored = await fa.federation.search(MemoryQuery(include_archived=True, limit=50))
    assert stored == [], "a 0.2-confidence entry must not reach the store"


# ---------------------------------------------------------------------------
# context confidence filter (entries < 0.5 never reach the prompt)
# ---------------------------------------------------------------------------


async def test_context_confidence_filter_excludes_subthreshold(make_federated_agent: MakeFederatedAgent):
    """A 0.4-confidence entry lives in the store but is excluded from the prompt."""
    fa = await make_federated_agent(summarise_on_close=False)
    mid = _entry(
        fa.agent_id,
        "MIDCONF the launch code is hunter2",
        importance=0.8,
        confidence=0.4,
        confidence_source=ConfidenceSource.AGENT,
    )
    await fa.federation.write(mid)

    # It is genuinely in the store (an unfiltered read finds it)...
    present = await fa.federation.search(MemoryQuery(keywords=["MIDCONF"], limit=10))
    assert [e.id for e in present] == [mid.id]

    # ...but a run does not inject it: 0.4 < the 0.5 context floor.
    await fa.agent.run("what is the launch code?")

    system = fa.gateway.complete.call_args[0][1].system
    assert "hunter2" not in system
    assert "Relevant Memory" not in system


# ---------------------------------------------------------------------------
# typed retention via decay (episodic ages out, semantic persists)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    reason=(
        "Retrieval applies no time-decay: memory half-lives are config only "
        "(DecayProfile / CardDecayConfig), and nothing computes effective "
        "importance from age or drops aged entries at read time. Both entries "
        "come back, so the episodic-decays assertion fails. A strict xfail so it "
        "flips to a real pass the moment decayed retrieval is wired."
    ),
    strict=True,
)
async def test_typed_retention_episodic_decays_semantic_persists(make_federated_agent: MakeFederatedAgent):
    """Given the decay profiles, an old EPISODIC entry should drop while SEMANTIC stays.

    Time is seeded (no sleeps): both entries are written ~120 days in the past.
    With EPISODIC's 14-day half-life vs SEMANTIC's 180-day, only the semantic
    entry should survive retrieval — once decay-at-read exists.
    """
    fa = await make_federated_agent(summarise_on_close=False)
    old = datetime.now(UTC) - timedelta(days=120)

    episodic = _entry(
        fa.agent_id,
        "EPISODICNOTE we met last tuesday",
        type=MemoryType.EPISODIC,
        importance=0.4,
        created_at=old,
        last_accessed_at=old,
    )
    semantic = _entry(
        fa.agent_id,
        "SEMANTICFACT the capital is Lisbon",
        type=MemoryType.SEMANTIC,
        importance=0.5,
        created_at=old,
        last_accessed_at=old,
    )
    await fa.federation.write(episodic)
    await fa.federation.write(semantic)

    results = await fa.federation.search(MemoryQuery(text="note fact", limit=10))
    contents = " ".join(e.content for e in results)
    assert "SEMANTICFACT" in contents
    assert "EPISODICNOTE" not in contents


# ---------------------------------------------------------------------------
# GLOBAL degrade: a failing promotion does not fail the run, and is reported
# ---------------------------------------------------------------------------


async def test_global_degrade_continues_and_reports(make_federated_agent: MakeFederatedAgent, tmp_path: Path):
    """A GLOBAL tier that rejects a promotion write degrades — the run still completes."""
    private = SQLiteAdapter(tmp_path / "degrade-private.db")
    await private.connect()
    global_backing = SQLiteAdapter(tmp_path / "degrade-global.db", quick_check_on_open=False)
    await global_backing.connect()
    failing_global = FailingAdapter(global_backing)  # fail_after=0 → every GLOBAL write fails

    # A promotion-eligible PRIVATE entry (importance ≥ 0.9) fans out to GLOBAL too.
    promote = _entry(None, "PROMOTED critical shared fact", importance=0.95)
    fa = await make_federated_agent(
        private=private,
        global_=failing_global,
        extractor=StubExtractor([promote]),
        summarise_on_close=False,
    )

    out = await fa.agent.run("share this widely")
    assert out == "Hello from the agent.", "the run must complete despite the GLOBAL failure"

    # PRIVATE kept its copy; GLOBAL got nothing; a degraded event names the leg.
    private_hits = await private.search(MemoryQuery(keywords=["PROMOTED"], limit=10))
    assert [e.content for e in private_hits] == ["PROMOTED critical shared fact"]
    global_hits = await global_backing.search(MemoryQuery(keywords=["PROMOTED"], scope=MemoryScope.GLOBAL, limit=10))
    assert global_hits == []

    assert fa.degraded, "a failed GLOBAL promotion must emit a MemoryDegradedEvent"
    event = fa.degraded[-1]
    assert event.tier == "global"
    assert event.operation == "promote"


# ---------------------------------------------------------------------------
# PRIVATE fail: a lost private write must surface (durability anchor)
# ---------------------------------------------------------------------------


async def test_private_write_failure_raises(make_federated_agent: MakeFederatedAgent, tmp_path: Path):
    """A failing PRIVATE tier raises ``MemoryWriteError`` from the federation write.

    Also pins the intentional asymmetry: ``Agent.run`` treats extraction as
    best-effort and swallows the same failure, so a user-facing turn still
    completes even when memory cannot persist.
    """
    backing = SQLiteAdapter(tmp_path / "private-fail.db")
    await backing.connect()
    failing_private = FailingAdapter(backing)  # every write fails
    fa = await make_federated_agent(private=failing_private, summarise_on_close=False)

    entry = _entry(fa.agent_id, "should not persist", type=MemoryType.EPISODIC)

    with pytest.raises(MemoryWriteError):
        await fa.federation.write(entry)

    # The real store stayed consistent — nothing partially landed.
    assert await backing.search(MemoryQuery(include_archived=True, limit=10)) == []

    # Best-effort extraction: the run does not surface the write failure.
    out = await fa.agent.run("anything")
    assert out == "Hello from the agent."


# ---------------------------------------------------------------------------
# prune reduces the store but never touches pinned / high-importance
# ---------------------------------------------------------------------------


async def test_prune_keeps_pinned_and_high_importance(make_federated_agent: MakeFederatedAgent):
    """A min-importance purge drops low-value entries; pinned and high survive."""
    fa = await make_federated_agent(summarise_on_close=False)
    aid = fa.agent_id

    keep_high = _entry(aid, "KEEP high value", importance=0.8)
    keep_pinned = _entry(aid, "KEEP pinned low", type=MemoryType.EPISODIC, importance=0.05, pinned=True)
    drop_low = _entry(aid, "DROP low value", type=MemoryType.EPISODIC, importance=0.05)
    for entry in (keep_high, keep_pinned, drop_low):
        await fa.federation.write(entry)

    report = await fa.federation.prune(PrunePolicy(min_importance=0.1, mode=PruneMode.PURGE))
    assert report.purged == 1

    remaining = {e.content for e in await fa.federation.search(MemoryQuery(include_archived=True, limit=50))}
    assert "KEEP high value" in remaining
    assert "KEEP pinned low" in remaining  # pinned survives despite importance 0.05
    assert "DROP low value" not in remaining


# ---------------------------------------------------------------------------
# summary memory written on session close
# ---------------------------------------------------------------------------


async def test_summary_memory_written_on_close(make_federated_agent: MakeFederatedAgent):
    """Closing a session distils ``session.summary`` and writes one consolidated memory."""
    fa = await make_federated_agent(summarise_on_close=True)
    session = Session(agent_id=fa.agent_id)

    await fa.agent.run("Remember that I prefer tea over coffee", session=session)

    assert session.summary, "close should distil a summary onto the session"
    assert session.memories_extracted, "close should record the consolidated memory id"

    stored = await fa.federation.search(MemoryQuery(include_archived=True, limit=50))
    assert any(e.content == session.summary for e in stored), "the summary should be a retrievable memory"


# ---------------------------------------------------------------------------
# GLOBAL vector tier (opt-in: needs the sqlite-vec extra)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_VECTOR, reason="sqlite-vec extra not installed; GLOBAL vector tier unavailable")
async def test_global_vector_tier_semantic_recall(make_federated_agent: MakeFederatedAgent):
    """A GLOBAL semantic memory is recalled into the prompt via the real vector tier.

    Assembled through ``build_federation`` with a deterministic fake embedder, so
    the semantic path is exercised end-to-end without a live embedding provider.
    """
    fa = await make_federated_agent(embedding=FakeEmbedding())

    fact = _entry(
        fa.agent_id,
        "the mission codename is Nightingale",
        importance=0.8,
        scope=MemoryScope.GLOBAL,
    )
    await fa.federation.write(fact)

    await fa.agent.run("what is the mission codename?")

    system = fa.gateway.complete.call_args[0][1].system
    assert "Nightingale" in system
