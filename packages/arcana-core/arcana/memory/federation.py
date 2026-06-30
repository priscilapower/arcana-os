"""MemoryFederation - the single interface agents talk to.

Turns a :class:`MemoryRouter`'s routing *decisions* into actual async I/O:

* **Fan-out writes** — one logical write lands in every tier the router selects
  (the private store; private + global when an entry is promoted; a shared pool).
* **Merged reads** — one query fans across all routed tiers concurrently, the
  results are deduplicated and re-ranked by the agent's memory weights, then
  truncated.

It implements the ``MemoryAdapter`` protocol itself, so an Agent holds a
federation exactly where it would hold any single backend — the underlying
tier topology stays invisible to callers.

Reads and writes treat failure differently, on purpose. A failed *read* tier is
dropped so the agent still sees what the other tiers returned — partial memory
beats none. A failed *write* surfaces to the caller: a lost write is data loss
they must know about. Fan-out writes are not transactional across tiers; a
private write may have committed when a later tier fails.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable
from uuid import UUID

from arcana.memory.router import MemoryRouter, TierBackend
from arcana.types import MemoryEntry, MemoryQuery, PrunePolicy, PruneReport

logger = logging.getLogger("arcana.memory.federation")


@runtime_checkable
class SupportsPrune(Protocol):
    """A backend that can prune itself. Read-only tiers won't satisfy this."""

    async def prune(self, policy: PrunePolicy) -> PruneReport: ...


class MemoryFederation:
    """Fan-out writes and merged reads across a router's memory tiers.

    Implements the ``MemoryAdapter`` protocol. Composes a ``MemoryRouter`` for
    every routing and ranking decision; this layer only performs the I/O.
    """

    def __init__(self, router: MemoryRouter) -> None:
        self._router = router

    # ------------------------------------------------------------------
    # MemoryAdapter protocol
    # ------------------------------------------------------------------

    async def write(self, entry: MemoryEntry) -> None:
        """Write an entry to every tier the router selects.

        When a target tier's scope differs from the entry's, a scope-rewritten
        copy is sent there instead — this is the promotion case, where a
        high-importance PRIVATE entry is also stored GLOBAL. The copy keeps the
        same ``id`` (idempotent) and drops ``pool_name``.

        Writes fan out concurrently and are not transactional across tiers: if a
        tier fails, the exception propagates while any already-issued writes
        stand.
        """
        targets = self._router.route_write(entry)
        await asyncio.gather(*(tier.adapter.write(self._payload_for(tier, entry)) for tier in targets))

    async def search(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Fan a query across all routed tiers, then merge, dedup, and rank.

        Tiers are queried concurrently. A tier that fails is logged and dropped
        rather than sinking the whole read. Results are deduplicated by ``id``
        (keeping the copy from the most-local tier in routing order) and ranked
        by the agent's memory weights, then truncated to ``query.limit``.
        """
        return await self._merge_ranked(query)

    async def stream_search(self, query: MemoryQuery) -> AsyncIterator[MemoryEntry]:
        """Yield the merged, ranked entries best-first, one at a time.

        Produces the same sequence as :meth:`search` but as an async generator,
        so a caller can stop iterating the moment it has what it needs (e.g. once
        a context budget is full) instead of materialising the whole list.

        Best-first order needs the full candidate pool, so the fan-out and rank
        still happen up front; the streaming is on the consumption side. An empty
        routing set yields nothing.
        """
        for entry in await self._merge_ranked(query):
            yield entry

    async def prune(self, policy: PrunePolicy) -> PruneReport:
        """Prune every tier whose backend supports it; aggregate the reports.

        Read-only backends (no ``prune`` method) are skipped. Like writes, a tier
        failure propagates — losing track of a destructive operation should
        surface rather than be silently swallowed.
        """
        prunable = [t.adapter for t in self._router.all_tiers() if isinstance(t.adapter, SupportsPrune)]
        reports: list[PruneReport] = await asyncio.gather(*(adapter.prune(policy) for adapter in prunable))
        return PruneReport(
            scanned=sum(r.scanned for r in reports),
            archived=sum(r.archived for r in reports),
            purged=sum(r.purged for r in reports),
            tiers=len(prunable),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _merge_ranked(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Fan out across routed tiers, drop failures, dedup, and rank.

        Shared by :meth:`search` and :meth:`stream_search` so their merge and
        ranking behaviour cannot drift.
        """
        tiers = self._router.route_read(query)
        if not tiers:
            return []

        results = await asyncio.gather(*(tier.adapter.search(query) for tier in tiers), return_exceptions=True)

        merged: dict[UUID, MemoryEntry] = {}
        for tier, result in zip(tiers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("memory tier %s failed during search: %s", _tier_label(tier), result)
                continue
            for entry in result:
                # First writer wins: route_read yields private → shared → global,
                # so an entry present in several tiers keeps its most-local copy.
                merged.setdefault(entry.id, entry)

        return self._router.rank(list(merged.values()), query)

    @staticmethod
    def _payload_for(tier: TierBackend, entry: MemoryEntry) -> MemoryEntry:
        """The entry as it should land in ``tier`` — rewritten on scope mismatch."""
        if tier.scope == entry.scope:
            return entry
        return entry.model_copy(update={"scope": tier.scope, "pool_name": None})


def _tier_label(tier: TierBackend) -> str:
    return f"{tier.scope.value}:{tier.pool_name}" if tier.pool_name else tier.scope.value
