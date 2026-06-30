"""MemoryRouter — the routing-policy layer over concrete tier backends.

Given an agent's memory weights, the router answers two questions:

* **Write:** which tier backend(s) does an entry belong in? Decided by its
  scope, plus importance-based promotion of high-value private entries to the
  global tier.
* **Read:** which tiers should a query fan across, and how should the merged
  candidates be ranked? Decided by scope, memory type, and the agent's weights.

The router is pure and synchronous. It returns routing *decisions* — lists of
backends to write to or read from — and a ranking over already-gathered
candidates. It never performs I/O; the federation layer awaits the adapters and
applies any scope rewrite a promotion target calls for.
"""

from dataclasses import dataclass

from arcana.memory.errors import MemoryRoutingError
from arcana.types import (
    MemoryAdapter,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryWeights,
)

#: Importance at or above which a PRIVATE entry is also written to GLOBAL.
#: Mirrors ``SQLiteAdapter.PROMOTION_THRESHOLD`` and
#: ``MemoryEntry.should_promote_to_global`` so the cross-tier rule reads the
#: same everywhere.
GLOBAL_PROMOTION_THRESHOLD: float = 0.9


@dataclass(frozen=True)
class TierBackend:
    """A registered memory backend and the scope it serves.

    ``pool_name`` is set only for ``SHARED`` backends; it names the pool this
    adapter stands in for.
    """

    scope: MemoryScope
    adapter: MemoryAdapter
    pool_name: str | None = None


class MemoryRouter:
    """Routes memory writes and reads across private, shared, and global tiers.

    A neutral set of weights (all 0.5) is used when none is supplied, so an
    agent with no card preferences still gets a stable importance-driven order.
    """

    def __init__(
        self,
        *,
        private: MemoryAdapter,
        global_: MemoryAdapter | None = None,
        pools: dict[str, MemoryAdapter] | None = None,
        weights: MemoryWeights | None = None,
    ) -> None:
        self._private = private
        self._global = global_
        self._pools: dict[str, MemoryAdapter] = dict(pools or {})
        self._weights = weights or MemoryWeights()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_pool(self, name: str, adapter: MemoryAdapter) -> None:
        """Register (or replace) the backend serving a named shared pool."""
        self._pools[name] = adapter

    # ------------------------------------------------------------------
    # Write routing
    # ------------------------------------------------------------------

    def route_write(self, entry: MemoryEntry) -> list[TierBackend]:
        """Return the tier backend(s) an entry must be written to.

        * ``PRIVATE`` → the private tier, plus the global tier when the entry is
          eligible for promotion (``importance >= 0.9``) and a global backend is
          registered. The caller writes a ``scope=GLOBAL`` copy to that target.
        * ``SHARED`` → the backend for ``entry.pool_name``.
        * ``GLOBAL`` → the global tier.

        Raises ``MemoryRoutingError`` when a required tier is missing: a SHARED
        write with no or unknown ``pool_name``, or a GLOBAL write with no global
        backend. A PRIVATE entry eligible for promotion but lacking a global
        backend is not an error — it simply stays private.
        """
        if entry.scope == MemoryScope.PRIVATE:
            targets = [TierBackend(MemoryScope.PRIVATE, self._private)]
            if entry.should_promote_to_global and self._global is not None:
                targets.append(TierBackend(MemoryScope.GLOBAL, self._global))
            return targets

        if entry.scope == MemoryScope.SHARED:
            return [self._require_pool(entry.pool_name)]

        if entry.scope == MemoryScope.GLOBAL:
            return [self._require_global()]

        raise MemoryRoutingError(f"unroutable scope: {entry.scope!r}")  # pragma: no cover

    # ------------------------------------------------------------------
    # Read routing
    # ------------------------------------------------------------------

    def route_read(self, query: MemoryQuery) -> list[TierBackend]:
        """Return the tier backend(s) a query should fan across.

        * ``scope is None`` (federated read) → every registered tier.
        * ``PRIVATE`` / ``GLOBAL`` → that single tier.
        * ``SHARED`` → the named pool if ``pool_name`` is set, else every pool.

        Reads degrade rather than raise: a tier that isn't registered (e.g. no
        global backend) is silently skipped. An explicitly named pool that does
        not exist is a caller error and raises ``MemoryRoutingError``.
        """
        if query.scope is None:
            return self.all_tiers()

        if query.scope == MemoryScope.PRIVATE:
            return [TierBackend(MemoryScope.PRIVATE, self._private)]

        if query.scope == MemoryScope.GLOBAL:
            return [TierBackend(MemoryScope.GLOBAL, self._global)] if self._global is not None else []

        if query.scope == MemoryScope.SHARED:
            if query.pool_name is not None:
                return [self._require_pool(query.pool_name)]
            return [TierBackend(MemoryScope.SHARED, a, pool_name=n) for n, a in self._pools.items()]

        raise MemoryRoutingError(f"unroutable scope: {query.scope!r}")  # pragma: no cover

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def rank(self, entries: list[MemoryEntry], query: MemoryQuery) -> list[MemoryEntry]:
        """Re-rank merged candidates by the agent's memory weights.

        Layers card preference on top of each adapter's own ordering:

            score = importance × weights.for_type(type)

        Pinned entries always sort first; ties break on score then recency.
        Assumes the input is already deduplicated by ``id`` (the federation
        merges per-tier results before ranking). Truncates to ``query.limit``.
        """

        def sort_key(entry: MemoryEntry) -> tuple[bool, float, float]:
            score = entry.importance * self._weights.for_type(entry.type)
            return (entry.pinned, score, entry.last_accessed_at.timestamp())

        ranked = sorted(entries, key=sort_key, reverse=True)
        return ranked[: query.limit]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def all_tiers(self) -> list[TierBackend]:
        """Every registered tier — private, each shared pool, then global.

        The same private → shared → global order a federated read fans across,
        and the set a store-wide operation (e.g. pruning) iterates.
        """
        tiers = [TierBackend(MemoryScope.PRIVATE, self._private)]
        tiers += [TierBackend(MemoryScope.SHARED, a, pool_name=n) for n, a in self._pools.items()]
        if self._global is not None:
            tiers.append(TierBackend(MemoryScope.GLOBAL, self._global))
        return tiers

    def _require_pool(self, pool_name: str | None) -> TierBackend:
        if pool_name is None:
            raise MemoryRoutingError("SHARED scope requires a pool_name")
        adapter = self._pools.get(pool_name)
        if adapter is None:
            raise MemoryRoutingError(f"no backend registered for shared pool {pool_name!r}")
        return TierBackend(MemoryScope.SHARED, adapter, pool_name=pool_name)

    def _require_global(self) -> TierBackend:
        if self._global is None:
            raise MemoryRoutingError("GLOBAL scope requires a global backend")
        return TierBackend(MemoryScope.GLOBAL, self._global)
