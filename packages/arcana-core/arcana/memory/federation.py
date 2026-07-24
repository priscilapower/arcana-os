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
from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable
from uuid import UUID

from arcana.memory.errors import GlobalDeleteRefused, MemoryWriteError, ReadOnlyTierDelete, TierWriteFailed
from arcana.memory.resilience import ResilientTier
from arcana.memory.router import MemoryRouter, TierBackend
from arcana.observability import MemoryDegradedEvent, MemoryOperation, emit_degraded
from arcana.types import (
    AdapterHealth,
    ForgetResult,
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    PrunePolicy,
    PruneReport,
)

logger = logging.getLogger("arcana.memory.federation")


@runtime_checkable
class SupportsPrune(Protocol):
    """A backend that can prune itself. Read-only tiers won't satisfy this."""

    async def prune(self, policy: PrunePolicy) -> PruneReport: ...


@runtime_checkable
class SupportsGet(Protocol):
    """A backend that can resolve a single entry by id. Not every tier can."""

    async def get(self, memory_id: UUID) -> MemoryEntry | None: ...


@runtime_checkable
class SupportsDelete(Protocol):
    """A backend that can delete a single entry by id. Read-only tiers cannot.

    Mirrors :class:`SupportsPrune`: an optional capability the federation probes
    for before routing a destructive op, so a delete never reaches a read-only
    tier (a mounted knowledge connector) that would not honour it.
    """

    async def delete(self, memory_id: UUID, *, hard: bool = True) -> bool: ...


class MemoryFederation:
    """Fan-out writes and merged reads across a router's memory tiers.

    Implements the ``MemoryAdapter`` protocol. Composes a ``MemoryRouter`` for
    every routing and ranking decision; this layer only performs the I/O.
    """

    def __init__(
        self,
        router: MemoryRouter,
        on_degraded: Callable[[MemoryDegradedEvent], None] | None = None,
    ) -> None:
        self._router = router
        # Write/promote degradation is emitted here (not in the tier wrapper),
        # since only this layer knows a leg is a promotion and whether a failure
        # is fatal (PRIVATE) or degraded (SHARED / GLOBAL).
        self._on_degraded = on_degraded or emit_degraded

    # ------------------------------------------------------------------
    # MemoryAdapter protocol
    # ------------------------------------------------------------------

    async def write(self, entry: MemoryEntry) -> None:
        """Write an entry to every tier the router selects, by failure policy.

        When a target tier's scope differs from the entry's, a scope-rewritten
        copy is sent there instead — this is the promotion case, where a
        high-importance PRIVATE entry is also stored GLOBAL. The copy keeps the
        same ``id`` (idempotent) and drops ``pool_name``.

        Writes fan out concurrently and are not transactional across tiers, and a
        failing tier is handled by its scope: PRIVATE is the durability anchor,
        so its failure raises ``MemoryWriteError``; a SHARED or GLOBAL failure
        degrades (the tier wrapper has already surfaced a ``MemoryDegradedEvent``)
        so the session proceeds. A degraded GLOBAL therefore pauses promotion
        without ever failing the private write.
        """
        targets = self._router.route_write(entry)
        results = await asyncio.gather(
            *(tier.adapter.write(self._payload_for(tier, entry)) for tier in targets),
            return_exceptions=True,
        )

        for tier, result in zip(targets, results, strict=True):
            if result is None:
                continue
            if isinstance(result, TierWriteFailed):
                if result.scope is MemoryScope.PRIVATE:
                    # Fatal, not a degradation — raise, do not emit a degraded event.
                    raise MemoryWriteError(f"private memory write failed for entry {entry.id}") from result.cause
                # SHARED / GLOBAL degrade. A scope-rewritten leg is a promotion.
                operation = "promote" if tier.scope != entry.scope else "write"
                self._emit_degraded(tier, operation, result)
                continue
            raise result  # an unwrapped backend or unexpected error — surface it

    def _emit_degraded(self, tier: TierBackend, operation: MemoryOperation, failure: TierWriteFailed) -> None:
        event = MemoryDegradedEvent(
            agent_id="",
            session_id="",
            tier=_tier_label(tier),
            operation=operation,
            reason=failure.reason,
            message=str(failure.cause),
        )
        self._on_degraded(event)

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

    async def health_check(self) -> AdapterHealth:
        """Aggregate health across all registered tiers. Never raises.

        The federation is usable as long as at least one tier is reachable —
        consistent with degraded reads returning partial context. Unhealthy
        tiers are named in ``message`` so a caller can see what dropped out.
        """
        tiers = self._router.all_tiers()
        probes = await asyncio.gather(*(tier.adapter.health_check() for tier in tiers), return_exceptions=True)
        unhealthy: list[str] = []
        any_healthy = False
        for tier, probe in zip(tiers, probes, strict=True):
            if isinstance(probe, AdapterHealth) and probe.healthy:
                any_healthy = True
            else:
                unhealthy.append(_tier_label(tier))
        message = "" if not unhealthy else f"degraded tiers: {', '.join(unhealthy)}"
        return AdapterHealth(adapter_id="federation", healthy=any_healthy, message=message)

    async def prune(self, policy: PrunePolicy) -> PruneReport:
        """Prune every tier whose backend supports it; aggregate the reports.

        Read-only backends (no ``prune`` method) are skipped. The resilient tier
        wrapper does not forward pruning, so we prune the backend it wraps
        directly: pruning is a destructive maintenance op that deliberately
        bypasses the read/write resilience path — a tier failure propagates,
        because losing track of a destructive operation should surface rather
        than be silently swallowed.
        """
        prunable = [inner for t in self._router.all_tiers() if (inner := _prunable_backend(t.adapter)) is not None]
        reports: list[PruneReport] = await asyncio.gather(*(adapter.prune(policy) for adapter in prunable))
        return PruneReport(
            scanned=sum(r.scanned for r in reports),
            archived=sum(r.archived for r in reports),
            purged=sum(r.purged for r in reports),
            tiers=len(prunable),
        )

    async def browse(self, query: MemoryQuery) -> list[MemoryEntry]:
        """List the routed tier(s)' entries by importance, with no embedder.

        The counterpart to :meth:`search` for "show me what's in here": a
        text-less query fans across the routed tiers, hitting each backend's
        non-semantic scan (SQLite ``filter_search`` / a folder's keyword scan), so
        it needs no embedding provider and works fully offline. Results are
        deduplicated by id and ordered by decayed effective importance — but,
        unlike ``search``, aged-out entries are **kept**: a listing must show every
        stored entry, including ones that have decayed below their consolidation
        threshold. Truncated to ``query.limit``.
        """
        merged = await self._gather_merged(query)
        return self._router.order_by_importance(merged, query)

    async def get(self, memory_id: UUID) -> MemoryEntry | None:
        """Resolve a single entry by id across all tiers, or ``None`` if unknown.

        Scans tiers in routing order (private → shared → global) and returns the
        first match, so an id present in several tiers resolves to its most-local
        copy. Tiers whose backend cannot resolve by id (no ``get``) are skipped.
        Drives ``inspect`` and underpins :meth:`forget`'s tier resolution.
        """
        tier = await self._find_owning_tier(memory_id)
        return None if tier is None else await _backend_get(tier.adapter, memory_id)

    async def forget(self, memory_id: UUID, *, hard: bool = True) -> ForgetResult:
        """Delete one entry by id from the tier that owns it.

        Resolves the id's owning tier, then enforces the ownership invariant in
        this one place: a GLOBAL entry is The World's to remove, so a delete there
        is refused (:class:`GlobalDeleteRefused`); a read-only tier (a mounted
        knowledge connector) cannot delete, so that too is refused
        (:class:`ReadOnlyTierDelete`). A PRIVATE or SHARED entry is deleted through
        its backend — ``hard`` purges (the default), ``hard=False`` archives.
        Returns ``ForgetResult(found=False)`` when no tier owned the id.
        """
        tier = await self._find_owning_tier(memory_id)
        if tier is None:
            return ForgetResult(found=False)
        if tier.scope is MemoryScope.GLOBAL:
            raise GlobalDeleteRefused(memory_id)
        backend = _unwrap(tier.adapter)
        if not isinstance(backend, SupportsDelete):
            raise ReadOnlyTierDelete(memory_id, _tier_label(tier))
        removed = await backend.delete(memory_id, hard=hard)
        return ForgetResult(found=removed, scope=tier.scope, pool_name=tier.pool_name, hard=hard)

    async def aclose(self) -> None:
        """Close every tier's backend connection. Safe to call more than once.

        Reaches past the router's resilience wrappers to the real backend and
        closes it when it supports ``aclose`` (read-only tiers may not). This is
        the teardown seam a caller uses to release the private SQLite handle and
        any vector store when a run ends, leaving no open connections behind.
        """
        for tier in self._router.all_tiers():
            backend = tier.adapter.inner if isinstance(tier.adapter, ResilientTier) else tier.adapter
            closer = getattr(backend, "aclose", None)
            if closer is not None:
                await closer()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _merge_ranked(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Fan out across routed tiers, drop failures, dedup, and rank.

        Shared by :meth:`search` and :meth:`stream_search` so their merge and
        ranking behaviour cannot drift.
        """
        merged = await self._gather_merged(query)
        return self._router.rank(merged, query)

    async def _gather_merged(self, query: MemoryQuery) -> list[MemoryEntry]:
        """Fan a query across routed tiers, drop failures, and dedup by id.

        The shared fan-out both :meth:`search` (which then card-ranks) and
        :meth:`browse` (which then orders by importance) build on, so their tier
        traversal and dedup behaviour cannot drift.
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

        return list(merged.values())

    async def _find_owning_tier(self, memory_id: UUID) -> TierBackend | None:
        """The tier that holds ``memory_id``, scanned private → shared → global.

        Uses each backend's by-id ``get`` (when it has one) so resolution never
        guesses the wrong store; the first tier to resolve the id wins, matching
        the most-local-copy rule reads follow. Returns ``None`` when no tier owns
        it. Tiers are probed sequentially and short-circuit on the first hit —
        cheap by-id lookups, and it stops at the private store for the common case.

        Like :meth:`prune`, the by-id ``get`` reaches past the resilience wrapper
        to the raw backend, so a delete/inspect is not bounded by a tier timeout —
        a destructive op should surface a backend failure rather than degrade to a
        silent no-op. The trade-off: resolving an id that only a slow mounted
        connector could own waits on that folder scan; the common private/shared
        hit short-circuits first.
        """
        for tier in self._router.all_tiers():
            if await _backend_get(tier.adapter, memory_id) is not None:
                return tier
        return None

    @staticmethod
    def _payload_for(tier: TierBackend, entry: MemoryEntry) -> MemoryEntry:
        """The entry as it should land in ``tier`` — rewritten on scope mismatch."""
        if tier.scope == entry.scope:
            return entry
        return entry.model_copy(update={"scope": tier.scope, "pool_name": None})


def _tier_label(tier: TierBackend) -> str:
    return f"{tier.scope.value}:{tier.pool_name}" if tier.pool_name else tier.scope.value


def _unwrap(adapter: MemoryAdapter) -> MemoryAdapter:
    """The real backend behind a tier, past the resilience wrapper."""
    return adapter.inner if isinstance(adapter, ResilientTier) else adapter


async def _backend_get(adapter: MemoryAdapter, memory_id: UUID) -> MemoryEntry | None:
    """Resolve an id through a tier's backend when it can, else ``None``.

    Unwraps the resilience layer and calls ``get`` only when the backend supports
    it — a tier that cannot resolve by id simply never owns the lookup.
    """
    backend = _unwrap(adapter)
    if isinstance(backend, SupportsGet):
        return await backend.get(memory_id)
    return None


def _prunable_backend(adapter: MemoryAdapter) -> SupportsPrune | None:
    """The prunable backend behind a tier, unwrapping the resilience layer.

    Returns the adapter itself (or the wrapper's inner) when it supports pruning,
    else ``None`` for read-only tiers.
    """
    backend = adapter.inner if isinstance(adapter, ResilientTier) else adapter
    return backend if isinstance(backend, SupportsPrune) else None
