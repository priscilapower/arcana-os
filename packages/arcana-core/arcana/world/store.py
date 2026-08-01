"""Reads the World's routing inputs from ``~/.arcana/`` — file-system-as-truth.

``world.json`` holds the routing rules, the world-level routing config, and a
pointer to the active Spread; Spreads themselves live under ``spreads/``. The
reader is deliberately fail-safe: a missing or corrupt file, or an individual
malformed rule/spread, degrades to sensible defaults rather than raising, so the
router can always resolve (never stranding a session on a bad config file).
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from arcana.types import RoutingConfig, RoutingRule, Spread

logger = logging.getLogger("arcana.world.store")


def _default_home() -> Path:
    return Path.home() / ".arcana"


class _WorldFile(BaseModel):
    """The raw shape of ``world.json``, tolerant of missing/extra keys.

    Fields are left untyped (``Any``) on purpose: the sub-parts are parsed
    defensively below so one malformed rule can't sink the rest, and an inline
    Spread or a bad ``active_spread`` pointer degrades to "no Spread" instead of
    failing the whole read.
    """

    model_config = ConfigDict(extra="ignore")

    active_spread: Any = None
    routing_rules: list[Any] = []
    default_agent_id: Any = None
    retry_window_s: Any = 60


@dataclass(frozen=True)
class LoadedWorld:
    """The routing inputs for one resolution, read together from disk."""

    rules: list[RoutingRule]
    config: RoutingConfig
    spread: Spread | None


class WorldStore:
    """Loads routing rules, config, and the active Spread from ``~/.arcana/``."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = home or _default_home()

    @property
    def world_json(self) -> Path:
        return self._home / "world.json"

    @property
    def spreads_dir(self) -> Path:
        return self._home / "spreads"

    def load(self) -> LoadedWorld:
        """Read rules, config, and the active Spread in a single pass.

        One ``world.json`` read plus at most one Spread-file read — bounded I/O,
        no per-rule filesystem access.
        """
        world = self._read_world_json()
        return LoadedWorld(
            rules=self._parse_rules(world.routing_rules),
            config=self._parse_config(world),
            spread=self._resolve_active_spread(world.active_spread),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_world_json(self) -> _WorldFile:
        path = self.world_json
        if not path.exists():
            return _WorldFile()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return _WorldFile.model_validate(raw)
        except (OSError, ValueError):
            logger.warning("world.json unreadable at %s; using defaults", path)
            return _WorldFile()

    def _parse_rules(self, raw: list[Any]) -> list[RoutingRule]:
        rules: list[RoutingRule] = []
        for entry in raw:
            try:
                rules.append(RoutingRule.model_validate(entry))
            except ValueError:
                logger.warning("skipping malformed routing rule: %r", entry)
        return rules

    def _parse_config(self, world: _WorldFile) -> RoutingConfig:
        try:
            return RoutingConfig.model_validate(
                {"default_agent_id": world.default_agent_id, "retry_window_s": world.retry_window_s}
            )
        except ValueError:
            logger.warning("invalid routing config in world.json; using defaults")
            return RoutingConfig()

    def _resolve_active_spread(self, raw: Any) -> Spread | None:
        """Resolve the ``active_spread`` pointer (a Spread id) to a Spread.

        Tolerates an inline Spread object too. Any failure — bad id, missing
        file, corrupt Spread — resolves to "no active Spread" so routing falls
        back to the all-agents pool.
        """
        if raw is None:
            return None
        if isinstance(raw, dict):
            try:
                return Spread.model_validate(raw)
            except ValueError:
                logger.warning("inline active_spread is malformed; ignoring")
                return None
        try:
            spread_id = UUID(str(raw))
        except (ValueError, TypeError):
            logger.warning("active_spread is not a valid id: %r", raw)
            return None
        path = self.spreads_dir / f"{spread_id}.json"
        if not path.exists():
            logger.warning("active spread %s has no file at %s", spread_id, path)
            return None
        try:
            return Spread.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("active spread %s is unreadable; ignoring", spread_id)
            return None
