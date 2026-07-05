"""build_federation — the single construction seam for a :class:`MemoryFederation`.

Turns an agent identity plus an on-disk ``~/.arcana`` home into a ready
``MemoryFederation``: a per-agent private SQLite store (the durability anchor),
an optional shared global vector store, and any configured shared pools, all
composed behind one ``MemoryAdapter`` via a :class:`MemoryRouter`.

Assembly lives here, not inside ``Agent``: the agent holds a ``MemoryAdapter``
and stays unaware of the tier topology, so it remains transport-agnostic and
unit-testable with a fake adapter. Callers (the CLI and the registry's runtime
builder) construct the federation through this seam and inject it.

The private store is opened here, which runs its pending schema migrations and
quarantines a corrupt store before the agent's first read or write. The global
tier is enabled only when an embedding provider is supplied; without one a
zero-config install stays private-SQLite-only rather than failing.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

from arcana.memory.adapters.sqlite import SQLiteAdapter
from arcana.memory.adapters.vector import VectorAdapter
from arcana.memory.embedding_gateway import EmbeddingGateway
from arcana.memory.extraction import ExtractionConfig
from arcana.memory.federation import MemoryFederation
from arcana.memory.router import MemoryRouter
from arcana.observability import MemoryDegradedEvent
from arcana.types import MemoryAdapter

logger = logging.getLogger("arcana.memory.assembly")


@dataclass(frozen=True)
class PoolConfig:
    """A shared pool to register on the federation: a name and its backend."""

    name: str
    adapter: MemoryAdapter


class MemoryConfig(BaseModel):
    """The ``memory`` block of ``~/.arcana/config.json``.

    An absent block yields these defaults, so an install that predates the block
    keeps working: memory on, private SQLite, global vector, no shared pools.
    ``global`` is a Python reserved word, so it maps to ``global_`` via alias.
    """

    enabled: bool = True
    private: str = "sqlite"
    global_: str = Field(default="vector", alias="global")
    pools: list[str] = Field(default_factory=list)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)

    model_config = {"populate_by_name": True}


def load_memory_config(home: Path) -> MemoryConfig:
    """Read the ``memory`` block from ``<home>/config.json``.

    A missing file or missing block yields all defaults (never raises), so a
    fresh install and an existing ``~/.arcana`` both resolve to a usable config.
    """
    config_path = home / "config.json"
    if not config_path.exists():
        return MemoryConfig()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return MemoryConfig()
    return MemoryConfig.model_validate(data.get("memory") or {})


async def build_federation(
    agent_id: UUID,
    *,
    home: Path,
    embedding: EmbeddingGateway | None = None,
    pools: list[PoolConfig] | None = None,
    on_degraded: Callable[[MemoryDegradedEvent], None] | None = None,
) -> MemoryFederation:
    """Assemble the tier stack for one agent and return its federation.

    * **PRIVATE** — per-agent SQLite at ``<home>/agents/{id}/memory.db``, the
      durability anchor. Opening it runs pending migrations and, on a corrupt
      file, quarantines the store before first use.
    * **GLOBAL** — a shared vector store at ``<home>/vector/global.db``, present
      only when ``embedding`` is given. It is semantic when the embedder is
      healthy and keyword (FTS5) when not; with no embedder the tier is dropped
      entirely and the agent runs private-only. Shared across agents, so its
      open-time integrity check is off (it detects corruption on read instead).
    * **SHARED** — each pool in ``pools`` is registered on the router by name.

    ``on_degraded`` defaults, inside the federation, to the observability audit
    log, so a degraded SHARED/GLOBAL tier is recorded without failing the run.
    """
    private_db = home / "agents" / str(agent_id) / "memory.db"
    private = SQLiteAdapter(private_db)
    await private.connect()  # opens, migrates, and integrity-checks the private store

    global_: MemoryAdapter | None = None
    if embedding is not None:
        global_store = SQLiteAdapter(home / "vector" / "global.db", quick_check_on_open=False)
        global_ = VectorAdapter(global_store, embedding)
        await global_.connect()
    else:
        logger.info("no embedding provider — global memory tier disabled (private SQLite only)")

    router = MemoryRouter(private=private, global_=global_)
    for pool in pools or []:
        router.register_pool(pool.name, pool.adapter)

    return MemoryFederation(router, on_degraded=on_degraded)
