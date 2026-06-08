from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeAlias


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


if TYPE_CHECKING:
    # Recursive JSON type — only visible to static type checkers.
    # Pydantic cannot resolve recursive TypeAlias at schema-generation time,
    # so at runtime we fall back to Any (same behaviour as before the change).
    JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
else:
    JsonValue = Any

# A JSON object — dict with string keys and JSON-compatible values.
JsonObject: TypeAlias = dict[str, Any]
