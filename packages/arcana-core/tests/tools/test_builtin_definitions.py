"""The registry and the adapter must offer the exact same builtin schemas."""

from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import ToolSubscription


def test_definitions_cover_the_two_network_builtins():
    assert set(BUILTIN_DEFINITIONS) == {"web_search", "fetch_url"}


async def test_registry_and_adapter_share_the_same_objects():
    # Same-source assertion: the model-visible definition and the executed one
    # are the identical object, so their schemas cannot drift.
    registry = MCPRegistry()
    subs = [ToolSubscription(qualified_name=f"builtin/{n}") for n in ("web_search", "fetch_url")]
    registry_defs = {d.name: d for d in registry.resolve(subs)}

    adapter = BuiltinToolAdapter(WebToolsConfig())
    provided = {d.name: d for d in adapter.provides()}

    for name in ("web_search", "fetch_url"):
        assert registry_defs[name] is BUILTIN_DEFINITIONS[name]
        assert provided[name] is BUILTIN_DEFINITIONS[name]

    await adapter.aclose()
