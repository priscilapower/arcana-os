"""Aggregated builtin tool schemas — the single source of truth across domains.

``BUILTIN_DEFINITIONS`` collects every builtin's ``ToolDefinition`` across the
domains that have one (web and filesystem). Both the model-visible registry
(``MCPRegistry._register_builtins``) and the executor (``BuiltinToolAdapter``)
consume this dict, so the schema a model is offered can never drift from the
schema the adapter honours.
"""

from arcana.tools.builtins.fs.definitions import (
    COPY,
    DELETE_DIR,
    DELETE_FILE,
    LIST_DIR,
    MAKE_DIR,
    MOVE,
    READ_FILE,
    WRITE_FILE,
)
from arcana.tools.builtins.web.definitions import FETCH_URL, WEB_SEARCH
from arcana.types.tool import ToolDefinition

# Keyed by tool name so the registry and adapter can share exact objects.
BUILTIN_DEFINITIONS: dict[str, ToolDefinition] = {
    d.name: d
    for d in (
        WEB_SEARCH,
        FETCH_URL,
        LIST_DIR,
        READ_FILE,
        WRITE_FILE,
        DELETE_FILE,
        MAKE_DIR,
        MOVE,
        COPY,
        DELETE_DIR,
    )
}
