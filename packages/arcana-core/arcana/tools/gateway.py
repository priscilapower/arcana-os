"""ToolGateway — resolves an agent's tool subscriptions, enforces permission, and routes calls.

Sits beside ``ModelGateway``. The ``MCPRegistry`` owns tool *definitions*; the
gateway owns *execution* by mapping a tool name to the ``ToolAdapter`` that can
run it. Permission is enforced in two places (defense-in-depth): the agent only
ever exposes subscribed tools to the model, and ``dispatch`` re-checks
membership before running anything.
"""

import asyncio
import time
from typing import Any

from pydantic import TypeAdapter, ValidationError

from arcana.models.adapters.base import ToolCallResult, ToolParam
from arcana.observability import get_tracer
from arcana.tools.adapters.base import BuiltinToolAdapter, ToolAdapter
from arcana.tools.config import DEFAULT_TOOL_TIMEOUT_S
from arcana.tools.registry import MCPRegistry, get_mcp_registry
from arcana.types.tool import ToolResult, ToolSubscription

# Tool-call arguments arrive as a JSON string from the model; validate them into
# a genuinely-typed object so a non-object payload is rejected as a failed result.
_ARGS_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


class ToolGateway:
    """Resolves subscriptions to request tools and dispatches tool calls to adapters."""

    def __init__(
        self,
        registry: MCPRegistry,
        adapters: list[ToolAdapter],
        *,
        timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> None:
        self._registry = registry
        self._adapters = list(adapters)
        self._timeout_s = timeout_s

    def tools_for(
        self,
        subscriptions: list[ToolSubscription],
        supports_tools: bool,
    ) -> list[ToolParam]:
        """Resolve subscriptions into request ``ToolParam``s.

        Definitions come from the registry (builtins + connected MCP servers)
        and from the registered adapters (e.g. the reference builtin), filtered
        to what the agent actually subscribed to. Returns an empty list when the
        model can't call tools, so the caller passes ``tools=None`` unchanged.
        """
        if not supports_tools:
            return []

        resolved: dict[str, ToolParam] = {
            d.name: ToolParam(name=d.name, description=d.description, input_schema=d.input_schema)
            for d in self._registry.resolve(subscriptions, supports_tools)
        }
        wanted = {sub.tool_name for sub in subscriptions}
        for adapter in self._adapters:
            for definition in adapter.provides():
                if definition.name in wanted and definition.name not in resolved:
                    resolved[definition.name] = ToolParam(
                        name=definition.name,
                        description=definition.description,
                        input_schema=definition.input_schema,
                    )
        return list(resolved.values())

    async def dispatch(self, call: ToolCallResult, *, allowed: set[str]) -> ToolResult:
        """Route a tool call to its adapter and return a ``ToolResult``.

        A call outside ``allowed`` is denied without executing. Unknown names,
        bad arguments, timeouts, and adapter exceptions all come back as a
        failed ``ToolResult`` so the model can recover — never as an exception.
        """
        name = call["function"]["name"]
        with get_tracer().start_as_current_span("tool.dispatch") as span:
            span.set_attribute("arcana.tool.name", name)

            if name not in allowed:
                span.set_attribute("arcana.tool.denied", True)
                span.set_attribute("arcana.tool.success", False)
                return ToolResult(tool_name=name, success=False, error="not permitted")

            try:
                args = _ARGS_ADAPTER.validate_json(call["function"]["arguments"] or "{}")
            except ValidationError as exc:
                return ToolResult(tool_name=name, success=False, error=f"invalid arguments: {exc}")

            adapter = self._route(name)
            if adapter is None:
                return ToolResult(tool_name=name, success=False, error="no adapter")

            start = time.monotonic()
            try:
                result = await asyncio.wait_for(adapter.execute(name, args), timeout=self._timeout_s)
            except TimeoutError:
                elapsed = int((time.monotonic() - start) * 1000)
                span.set_attribute("arcana.tool.success", False)
                return ToolResult(tool_name=name, success=False, error="timeout", duration_ms=elapsed)
            except Exception as exc:
                elapsed = int((time.monotonic() - start) * 1000)
                span.set_attribute("arcana.tool.success", False)
                return ToolResult(tool_name=name, success=False, error=str(exc), duration_ms=elapsed)

            if not result.duration_ms:
                result.duration_ms = int((time.monotonic() - start) * 1000)
            span.set_attribute("arcana.tool.success", result.success)
            span.set_attribute("arcana.tool.duration_ms", result.duration_ms)
            return result

    def _route(self, name: str) -> ToolAdapter | None:
        """First registered adapter that supports ``name``, or None."""
        return next((a for a in self._adapters if a.supports(name)), None)


def default_tool_gateway() -> ToolGateway:
    """A ToolGateway wired to the global MCP registry and the builtin adapter."""
    return ToolGateway(get_mcp_registry(), [BuiltinToolAdapter()])
