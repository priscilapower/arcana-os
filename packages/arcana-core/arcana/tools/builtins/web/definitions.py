"""Schemas for the web builtins (``web_search`` / ``fetch_url``).

These are the single source of truth for these two tools' schemas: they feed the
aggregated ``BUILTIN_DEFINITIONS`` (see :mod:`arcana.tools.builtins.definitions`)
that both the model-visible registry and the executing adapter consume, so the
schema a model is offered can never drift from the schema the adapter honours.
"""

from arcana.types.tool import ToolDefinition, ToolType

WEB_SEARCH = ToolDefinition(
    name="web_search",
    type=ToolType.BUILTIN,
    description="Search the web and return ranked results (title, url, snippet).",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
)

FETCH_URL = ToolDefinition(
    name="fetch_url",
    type=ToolType.BUILTIN,
    description="Fetch a URL over HTTP(S) and return its readable text content.",
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string", "format": "uri"}},
        "required": ["url"],
    },
)
