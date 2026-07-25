"""The registry and the adapter must offer the exact same builtin schemas."""

from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import BuiltinTool, DeleteOutcome, FsEntryKind, ToolResult, ToolSubscription, ToolType


def test_definitions_cover_every_executable_builtin():
    assert set(BUILTIN_DEFINITIONS) == {
        BuiltinTool.WEB_SEARCH,
        BuiltinTool.FETCH_URL,
        BuiltinTool.LIST_DIR,
        BuiltinTool.READ_FILE,
        BuiltinTool.WRITE_FILE,
        BuiltinTool.DELETE_FILE,
        BuiltinTool.MAKE_DIR,
        BuiltinTool.MOVE,
        BuiltinTool.COPY,
        BuiltinTool.DELETE_DIR,
        BuiltinTool.RUN_CODE,
    }


def test_every_path_taking_builtin_declares_its_path_args():
    """A path arg a definition does not declare is a path a scope never checks.

    The failure is silent — the call runs, the guardrail simply had nothing to
    say about the argument — so the roster is asserted rather than trusted.
    """
    assert {name: tuple(d.path_args) for name, d in BUILTIN_DEFINITIONS.items() if d.path_args} == {
        BuiltinTool.LIST_DIR: ("path",),
        BuiltinTool.READ_FILE: ("path",),
        BuiltinTool.WRITE_FILE: ("path",),
        BuiltinTool.DELETE_FILE: ("path",),
        BuiltinTool.MAKE_DIR: ("path",),
        BuiltinTool.MOVE: ("src", "dst"),
        BuiltinTool.COPY: ("src", "dst"),
        BuiltinTool.DELETE_DIR: ("path",),
    }


def test_every_declared_path_arg_exists_in_the_schema():
    # A declared arg the schema does not offer would scope an argument the model
    # can never send — a rule that reads as enforcement and is not.
    for definition in BUILTIN_DEFINITIONS.values():
        properties = definition.input_schema.get("properties")
        assert isinstance(properties, dict)
        for arg in definition.path_args:
            assert arg in properties, f"{definition.name} declares '{arg}' but its schema has no such property"


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


def test_result_enums_serialize_as_their_bare_wire_values():
    # These land in a persisted ToolResult and are read back by name, so adopting
    # an enum for them must not have changed a single byte of that JSON.
    result = ToolResult(
        tool_name=BuiltinTool.DELETE_DIR,
        success=True,
        output={"outcome": DeleteOutcome.TRASHED, "kind": FsEntryKind.DIR},
    )

    assert '"outcome":"trashed"' in result.model_dump_json()
    assert '"kind":"dir"' in result.model_dump_json()


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
