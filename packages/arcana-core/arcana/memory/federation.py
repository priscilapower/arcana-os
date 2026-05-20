"""
MemoryFederation — three-tier, decay-aware, quality-filtered memory.

Write pipeline:
  1. Confidence check — entries below min_confidence_to_store are rejected
  2. Scope routing — private / shared pool(s) / global
  3. Conflict detection — on SHARED writes, checks for near-duplicates
  4. Auto-promotion — importance >= 0.9 promotes to GLOBAL

Search pipeline:
  1. Fan out to accessible tiers
  2. Filter by min_confidence (excludes potentially poisoned entries)
  3. Exclude conflicted entries by default
  4. Rank by effective_importance() — decay-aware, not raw importance

Whiteboards:
  Created by WorldEngine at automation run / spread start.
  Cleaned up by WorldEngine after run ends.
  Not stored in the federation — managed separately by WorldEngine.

Conflict resolution:
  Detection happens here (on write).
  Resolution happens in WorldEngine (Epic 7).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from arcana.memory.adapters.base import MemoryAdapter
from arcana.memory.decay import decay_sorted
from arcana.types.card import Card
from arcana.types.memory import (
    AdapterHealth,
    MemoryConflict,
    MemoryEntry,
    MemoryProfile,
    MemoryQuery,
    MemoryScope,
)

GLOBAL_PROMOTION_THRESHOLD = 0.9

# Similarity threshold above which two entries are considered conflicting.
# TODO: implement embedding-based similarity check in Epic 7.
# For now, exact string match on content (simplified).
CONFLICT_SIMILARITY_THRESHOLD = 0.85


class SharedMemoryPool:
    """
    A named memory store multiple agents can read and write.

    Example:
        pool = SharedMemoryPool(
            name="project-arcana",
            adapter=SQLiteAdapter("~/.arcana/memory/shared/project-arcana.db"),
        )
        researcher = Agent(..., shared_pool_names=["project-arcana"])
        writer     = Agent(..., shared_pool_names=["project-arcana"])
    """

    def __init__(
        self,
        name: str,
        adapter: MemoryAdapter,
        workspace_id: str = "local",
    ) -> None:
        self.name = name
        self.adapter = adapter
        self.workspace_id = workspace_id  # always "local" in Phase 1/2
        self._conflicts: list[MemoryConflict] = []   # in-memory until Epic 7 persists

    async def connect(self) -> None:
        await self.adapter.connect()

    async def close(self) -> None:
        await self.adapter.close()

    @property
    def open_conflicts(self) -> list[MemoryConflict]:
        return [c for c in self._conflicts if c.status.value == "open"]


class MemoryFederation:
    """
    Unified, decay-aware, quality-filtered memory interface.

    search() results are:
      - ranked by effective_importance() (decay-aware)
      - filtered by min_confidence (anti-poisoning)
      - deduplicated across tiers
      - conflicted entries excluded by default
    """

    def __init__(
        self,
        private: MemoryAdapter,
        shared_pools: list[SharedMemoryPool] | None = None,
        global_store: MemoryAdapter | None = None,
        agent_card: Card | None = None,
        memory_profile: MemoryProfile | None = None,
    ) -> None:
        self._private = private
        self._shared_pools = shared_pools or []
        self._global_store = global_store
        self._agent_card = agent_card
        self._memory_profile = memory_profile

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        targets = [self._private, *[p.adapter for p in self._shared_pools]]
        if self._global_store:
            targets.append(self._global_store)
        await asyncio.gather(*[t.connect() for t in targets])

    async def close(self) -> None:
        targets = [self._private, *[p.adapter for p in self._shared_pools]]
        if self._global_store:
            targets.append(self._global_store)
        await asyncio.gather(*[t.close() for t in targets])

    # ------------------------------------------------------------------
    # Write — confidence check + conflict detection
    # ------------------------------------------------------------------

    async def write(self, entry: MemoryEntry) -> str | None:
        """
        Write a memory entry through the quality pipeline.

        Returns entry id on success, None if rejected by confidence threshold.
        Conflict detection runs on SHARED writes.
        """
        # 1. Confidence gate — reject likely hallucinations
        min_confidence = (
            self._memory_profile.min_confidence_to_store
            if self._memory_profile else 0.3
        )
        if entry.confidence < min_confidence:
            # TODO: log rejected entry for observability in Epic 7
            return None

        # 2. Conflict detection on shared writes
        if entry.scope == MemoryScope.SHARED and entry.pool_name:
            await self._check_for_conflicts(entry)

        # 3. Route to appropriate tiers
        tasks = [self._private.write(entry)]

        if entry.scope == MemoryScope.SHARED and entry.pool_name:
            pool = self._get_pool(entry.pool_name)
            if pool:
                tasks.append(pool.adapter.write(entry))

        if (
            entry.scope == MemoryScope.GLOBAL
            or entry.importance >= GLOBAL_PROMOTION_THRESHOLD
        ) and self._global_store:
            tasks.append(self._global_store.write(entry))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, str):
                return r
        raise RuntimeError(f"All adapters failed to write entry {entry.id}")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def read(self, entry_id: str) -> MemoryEntry | None:
        result = await self._private.read(entry_id)
        if result:
            result.bump_access()
            return result
        for pool in self._shared_pools:
            result = await pool.adapter.read(entry_id)
            if result:
                result.bump_access()
                return result
        if self._global_store:
            return await self._global_store.read(entry_id)
        return None

    async def forget(self, entry_id: str) -> None:
        targets = [self._private, *[p.adapter for p in self._shared_pools]]
        if self._global_store:
            targets.append(self._global_store)
        await asyncio.gather(
            *[t.forget(entry_id) for t in targets], return_exceptions=True
        )

    async def pin(self, entry_id: str) -> None:
        """Pin a memory so it never decays or gets consolidated."""
        entry = await self.read(entry_id)
        if entry:
            entry.pinned = True
            await self.write(entry)

    async def confirm(self, entry_id: str) -> None:
        """
        Mark an entry as user-confirmed, boosting confidence to 1.0.
        Resolves any open conflict involving this entry.
        """
        from arcana.types.memory import ConfidenceSource
        entry = await self.read(entry_id)
        if entry:
            entry.confidence = 1.0
            entry.confidence_source = ConfidenceSource.USER_CONFIRMED
            entry.has_conflict = False
            await self.write(entry)

    # ------------------------------------------------------------------
    # Search — decay-aware + quality-filtered
    # ------------------------------------------------------------------

    async def search(
        self,
        query: MemoryQuery,
        now: datetime | None = None,
    ) -> list[MemoryEntry]:
        """
        Fan out, merge, deduplicate, filter, and rank.

        Filtering:
          - min_confidence: excludes potentially poisoned entries
          - include_conflicted=False (default): excludes conflicted entries
          - decay-aware ranking: stale entries rank below fresh ones
        """
        adapters = self._resolve_search_adapters(query)

        all_results = await asyncio.gather(
            *[a.search(query) for a in adapters], return_exceptions=True
        )

        # Resolve min_confidence: query param or profile default
        min_confidence = query.min_confidence
        if min_confidence == 0.0 and self._memory_profile:
            min_confidence = self._memory_profile.min_confidence_for_context

        seen: set[str] = set()
        merged: list[MemoryEntry] = []
        for result in all_results:
            if isinstance(result, Exception):
                continue
            for entry in result:
                eid = str(entry.id)
                if eid in seen:
                    continue
                seen.add(eid)

                # Quality filters
                if entry.confidence < min_confidence:
                    continue
                if entry.has_conflict and not query.include_conflicted:
                    continue

                merged.append(entry)

        ranked = decay_sorted(merged, self._memory_profile, now)
        return ranked[: query.limit]

    # ------------------------------------------------------------------
    # Conflict access
    # ------------------------------------------------------------------

    def get_open_conflicts(self) -> list[MemoryConflict]:
        """Return all open conflicts across all shared pools."""
        conflicts = []
        for pool in self._shared_pools:
            conflicts.extend(pool.open_conflicts)
        return conflicts

    # ------------------------------------------------------------------
    # Consolidation support
    # ------------------------------------------------------------------

    async def get_consolidation_candidates(
        self, now: datetime | None = None
    ) -> list[MemoryEntry]:
        from arcana.memory.decay import consolidation_candidates
        query = MemoryQuery(limit=10_000, include_archived=False)
        all_entries = await self.search(query, now=now)
        return consolidation_candidates(all_entries, self._memory_profile, now)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> list[AdapterHealth]:
        targets = [self._private, *[p.adapter for p in self._shared_pools]]
        if self._global_store:
            targets.append(self._global_store)
        return list(await asyncio.gather(*[t.health_check() for t in targets]))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _check_for_conflicts(self, incoming: MemoryEntry) -> None:
        """
        Check if a new SHARED entry conflicts with existing entries.
        Simplified for scaffold: flags near-duplicate content strings.
        Epic 7: replace with embedding cosine similarity check.

        If a conflict is detected:
          - Both entries are flagged with has_conflict=True
          - A MemoryConflict record is created on the pool
        """
        if not incoming.pool_name:
            return
        pool = self._get_pool(incoming.pool_name)
        if not pool:
            return

        # Simplified conflict check — same type, similar content length
        # TODO: Epic 7 — replace with embedding similarity >= CONFLICT_SIMILARITY_THRESHOLD
        query = MemoryQuery(
            type=incoming.type,
            scope=MemoryScope.SHARED,
            pool_name=incoming.pool_name,
            limit=20,
        )
        existing = await pool.adapter.search(query)

        for existing_entry in existing:
            if self._is_potentially_conflicting(incoming, existing_entry):
                # Flag both entries
                incoming.has_conflict = True
                existing_entry.has_conflict = True

                # Create conflict record
                conflict = MemoryConflict(
                    pool_name=incoming.pool_name,
                    entry_a_id=existing_entry.id,
                    entry_b_id=incoming.id,
                    similarity_score=0.0,  # TODO: real score in Epic 7
                )
                incoming.conflict_id = conflict.id
                existing_entry.conflict_id = conflict.id

                # Persist the flagged existing entry
                await pool.adapter.write(existing_entry)

                # Store conflict record on pool (in-memory for now)
                pool._conflicts.append(conflict)
                break  # flag the first conflict found; World resolves the rest

    def _is_potentially_conflicting(
        self, a: MemoryEntry, b: MemoryEntry
    ) -> bool:
        """
        Simplified conflict heuristic for scaffold.
        Epic 7: replace with proper embedding similarity.
        """
        if a.type != b.type:
            return False
        # Very naive: same first 50 chars suggests same topic, different content
        a_prefix = a.content[:50].lower().strip()
        b_prefix = b.content[:50].lower().strip()
        return (
            a_prefix == b_prefix
            and a.content.lower() != b.content.lower()
        )

    def _get_pool(self, name: str) -> SharedMemoryPool | None:
        return next((p for p in self._shared_pools if p.name == name), None)

    def _resolve_search_adapters(self, query: MemoryQuery) -> list[MemoryAdapter]:
        if query.scope == MemoryScope.PRIVATE:
            return [self._private]
        if query.scope == MemoryScope.SHARED:
            if query.pool_name:
                pool = self._get_pool(query.pool_name)
                return [pool.adapter] if pool else []
            return [p.adapter for p in self._shared_pools]
        if query.scope == MemoryScope.GLOBAL:
            return [self._global_store] if self._global_store else []
        adapters = [self._private, *[p.adapter for p in self._shared_pools]]
        if self._global_store:
            adapters.append(self._global_store)
        return adapters
