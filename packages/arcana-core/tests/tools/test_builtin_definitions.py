"""The registry and the adapter must offer the exact same builtin schemas."""

from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import BuiltinTool, ToolSubscription, ToolType


def test_definitions_cover_every_executable_builtin():
    assert set(BUILTIN_DEFINITIONS) == {
        BuiltinTool.WEB_SEARCH,
        BuiltinTool.FETCH_URL,
        BuiltinTool.LIST_DIR,
        BuiltinTool.READ_FILE,
        BuiltinTool.WRITE_FILE,
        BuiltinTool.DELETE_FILE,
    }


def test_every_registered_builtin_is_named_by_the_enum():
    """The enum is the roster: a builtin the registry offers must have a member.

    This is what stops a new tool from being added as a bare string and drifting
    out of reach of the guardrail rules and card defaults that name it.
    """
    registry = MCPRegistry()
    registry.load()
    registered = {t.name for t in registry.list_all_tools() if t.type == ToolType.BUILTIN}

    assert registered == {member.value for member in BuiltinTool}


def test_qualified_is_the_subscription_form():
    assert BuiltinTool.DELETE_FILE.qualified == "builtin/delete_file"
    assert ToolSubscription(qualified_name=BuiltinTool.DELETE_FILE.qualified).is_builtin


def test_members_are_their_wire_names():
    # Persisted subscriptions and guardrail rules hold these strings, so a
    # member must stay interchangeable with the bare name it serializes to.
    assert BuiltinTool.WRITE_FILE == "write_file"
    assert f"{BuiltinTool.WRITE_FILE}" == "write_file"
    assert BUILTIN_DEFINITIONS[BuiltinTool.WRITE_FILE].name == "write_file"


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
