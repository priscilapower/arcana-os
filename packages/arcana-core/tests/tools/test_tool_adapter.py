"""Tests for the ToolAdapter ABC and the builtin reference adapter."""

from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.types.tool import ToolType


def test_provides_returns_reference_tool():
    adapter = BuiltinToolAdapter()
    names = [d.name for d in adapter.provides()]
    assert names == ["echo"]
    assert adapter.type == ToolType.BUILTIN


def test_supports_matches_provided_definitions():
    adapter = BuiltinToolAdapter()
    assert adapter.supports("echo") is True
    assert adapter.supports("web_search") is False


async def test_execute_echo_returns_successful_result():
    adapter = BuiltinToolAdapter()
    result = await adapter.execute("echo", {"message": "hello"})
    assert result.success is True
    assert result.tool_name == "echo"
    assert result.output == "hello"
    assert result.error is None


async def test_execute_unknown_tool_returns_failure():
    adapter = BuiltinToolAdapter()
    result = await adapter.execute("nope", {})
    assert result.success is False
    assert result.error is not None
    assert "nope" in result.error


async def test_execute_echo_defaults_missing_message():
    adapter = BuiltinToolAdapter()
    result = await adapter.execute("echo", {})
    assert result.success is True
    assert result.output == ""
