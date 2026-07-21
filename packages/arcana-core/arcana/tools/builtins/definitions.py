"""Aggregated builtin tool schemas — the single source of truth across domains.

``BUILTIN_DEFINITIONS`` collects every builtin's ``ToolDefinition`` (web tools
today; filesystem/exec tools join here as their domains land). Both the
model-visible registry (``MCPRegistry._register_builtins``) and the executor
(``BuiltinToolAdapter``) consume this dict, so the schema a model is offered can
never drift from the schema the adapter honours.
"""

from arcana.tools.builtins.web.definitions import FETCH_URL, WEB_SEARCH
from arcana.types.tool import ToolDefinition

# Keyed by tool name so the registry and adapter can share exact objects.
BUILTIN_DEFINITIONS: dict[str, ToolDefinition] = {d.name: d for d in (WEB_SEARCH, FETCH_URL)}
