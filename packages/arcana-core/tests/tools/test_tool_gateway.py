"""Tests for the ToolGateway — resolution, permission, routing, and timeout."""

import asyncio
from typing import Any

from arcana.models.adapters.base import FunctionCall, ToolCallResult
from arcana.tools.adapters.base import BuiltinToolAdapter, ToolAdapter
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.tool import ToolDefinition, ToolResult, ToolSubscription, ToolType


def _call(name: str, arguments: str = "{}") -> ToolCallResult:
    return ToolCallResult(id="call-1", type="function", function=FunctionCall(name=name, arguments=arguments))


class SpyAdapter(ToolAdapter):
    """Records whether execute() was called — to prove denied calls never run."""

    type = ToolType.BUILTIN

    def __init__(self, *, sleep_s: float = 0.0) -> None:
        self.executed: list[str] = []
        self._sleep_s = sleep_s

    def provides(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="spy",
                description="records calls",
                input_schema={"type": "object", "properties": {}},
                type=ToolType.BUILTIN,
            )
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        self.executed.append(name)
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        return ToolResult(tool_name=name, success=True, output="ran")


def _gateway(adapters: list[ToolAdapter] | None = None, **kwargs: Any) -> ToolGateway:
    return ToolGateway(MCPRegistry(), adapters if adapters is not None else [BuiltinToolAdapter()], **kwargs)


# ---------------------------------------------------------------------------
# tools_for
# ---------------------------------------------------------------------------


def test_tools_for_resolves_adapter_provided_builtin():
    gw = _gateway()
    tools = gw.tools_for([ToolSubscription(qualified_name="builtin/echo")], supports_tools=True)
    assert [t["name"] for t in tools] == ["echo"]
    assert tools[0]["input_schema"]["properties"]["message"] == {"type": "string"}


def test_tools_for_resolves_registry_builtin():
    gw = _gateway()
    tools = gw.tools_for([ToolSubscription(qualified_name="builtin/web_search")], supports_tools=True)
    assert [t["name"] for t in tools] == ["web_search"]


def test_tools_for_only_returns_subscribed():
    gw = _gateway()
    tools = gw.tools_for([ToolSubscription(qualified_name="builtin/echo")], supports_tools=True)
    assert [t["name"] for t in tools] == ["echo"]  # web_search not subscribed → absent


def test_tools_for_empty_when_model_lacks_tool_support():
    gw = _gateway()
    tools = gw.tools_for([ToolSubscription(qualified_name="builtin/echo")], supports_tools=False)
    assert tools == []


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_routes_and_executes():
    gw = _gateway()
    result = await gw.dispatch(_call("echo", '{"message": "hi"}'), allowed={"echo"})
    assert result.success is True
    assert result.output == "hi"
    assert result.duration_ms >= 0


async def test_dispatch_denies_unsubscribed_without_executing():
    spy = SpyAdapter()
    gw = _gateway([spy])
    result = await gw.dispatch(_call("spy"), allowed=set())
    assert result.success is False
    assert result.error == "not permitted"
    assert spy.executed == []  # never ran


async def test_dispatch_unknown_tool_reports_no_adapter():
    gw = _gateway()
    result = await gw.dispatch(_call("ghost"), allowed={"ghost"})
    assert result.success is False
    assert result.error == "no adapter"


async def test_dispatch_invalid_arguments_returns_error():
    gw = _gateway()
    result = await gw.dispatch(_call("echo", "not json"), allowed={"echo"})
    assert result.success is False
    assert result.error is not None
    assert "invalid arguments" in result.error


async def test_dispatch_timeout_returns_error_result():
    spy = SpyAdapter(sleep_s=1.0)
    gw = _gateway([spy], timeout_s=0.01)
    result = await gw.dispatch(_call("spy"), allowed={"spy"})
    assert result.success is False
    assert result.error == "timeout"


async def test_dispatch_adapter_exception_becomes_failed_result():
    class Boom(ToolAdapter):
        type = ToolType.BUILTIN

        def provides(self) -> list[ToolDefinition]:
            return [
                ToolDefinition(name="boom", description="", input_schema={"type": "object"}, type=ToolType.BUILTIN)
            ]

        async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
            raise RuntimeError("kaboom")

    gw = _gateway([Boom()])
    result = await gw.dispatch(_call("boom"), allowed={"boom"})
    assert result.success is False
    assert result.error is not None
    assert "kaboom" in result.error
