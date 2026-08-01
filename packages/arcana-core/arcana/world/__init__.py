"""The World Engine — deterministic task routing over registered agents.

:class:`WorldEngine` answers *which agent runs this* by layering an explicit
bypass, keyword/regex rules, and a default over the active Spread's candidate
pool, recording each :class:`~arcana.types.RoutingDecision` to an append-only
audit before the agent runs. The selection itself is the pure, side-effect-free
:func:`Router.resolve`; the engine adds the I/O around it.
"""

from arcana.world.audit import RoutingAuditLog
from arcana.world.config import DEFAULT_TASK_PREVIEW_CHARS, RoutingTunables
from arcana.world.engine import WorldEngine
from arcana.world.router import NoRouteAskUser, Router
from arcana.world.store import LoadedWorld, WorldStore

__all__ = [
    "WorldEngine",
    "Router",
    "NoRouteAskUser",
    "WorldStore",
    "LoadedWorld",
    "RoutingAuditLog",
    "RoutingTunables",
    "DEFAULT_TASK_PREVIEW_CHARS",
]
