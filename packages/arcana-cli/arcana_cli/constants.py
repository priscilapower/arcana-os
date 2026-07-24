"""Package-wide constants shared across commands and UI modules.

Runtime state lives under ``~/.arcana`` by default; setting the ``ARCANA_HOME``
environment variable relocates it (agents, sessions, connections, secrets),
which is handy for separate profiles, CI, and containers. It's read once at
import — the standard state-root override convention (cf. ``GIT_DIR``,
``CARGO_HOME``), not a general settings surface.
"""

import os
from collections.abc import Mapping
from pathlib import Path

ARCANA_HOME: Path = Path(os.environ.get("ARCANA_HOME", Path.home() / ".arcana"))
AGENTS_BASE: Path = ARCANA_HOME / "agents"
CONNECTIONS_PATH: Path = ARCANA_HOME / "connections" / "models.json"
MCPS_PATH: Path = ARCANA_HOME / "connections" / "mcps.json"
MEMORY_ADAPTERS_PATH: Path = ARCANA_HOME / "connections" / "memory-adapters.json"

ROMAN: Mapping[int, str] = {
    0: "0",
    1: "I",
    2: "II",
    3: "III",
    4: "IV",
    5: "V",
    6: "VI",
    7: "VII",
    8: "VIII",
    9: "IX",
    10: "X",
    11: "XI",
    12: "XII",
    13: "XIII",
    14: "XIV",
    15: "XV",
    16: "XVI",
    17: "XVII",
    18: "XVIII",
    19: "XIX",
    20: "XX",
    21: "XXI",
}
