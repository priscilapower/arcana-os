"""Package-wide constants shared across commands and UI modules."""

from collections.abc import Mapping
from pathlib import Path

ARCANA_HOME: Path = Path.home() / ".arcana"
AGENTS_BASE: Path = ARCANA_HOME / "agents"
CONNECTIONS_PATH: Path = ARCANA_HOME / "connections" / "models.json"

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
