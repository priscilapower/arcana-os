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
from arcana.tools.adapters.mcp import MCPToolAdapter
from arcana.tools.config import DEFAULT_TOOL_TIMEOUT_S
from arcana.tools.registry import MCPRegistry, get_mcp_registry
from arcana.types.tool import ToolDefinition, ToolResult, ToolSubscription

# Tool-call arguments arrive as a JSON string from the model; validate them into
# a genuinely-typed object so a non-object payload is rejected as a failed result.
_ARGS_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


def _wire_name(qualified_name: str) -> str:
    """Sanitise a qualified name for a provider function schema.

    Provider tool-name grammars (``^[a-zA-Z0-9_-]{1,64}$``) reject the ``/`` in
    ``notion-mcp/search_pages``, so the wire form uses ``__``. It is a pure
    presentation concern — ``server/tool`` stays the canonical name everywhere
    else, restored from the ``wire→qualified`` map before permission and routing.
    """
    return qualified_name.replace("/", "__")


def _subscription_qualified(sub: ToolSubscription) -> str:
    """The canonical ``server/tool`` (or bare builtin) name a subscription names.

    Mirrors :attr:`ToolDefinition.qualified_name`: an MCP subscription keeps its
    ``server/tool`` form; a builtin drops the ``builtin/`` prefix to the bare
    tool name, matching how adapters name their builtin definitions.
    """
    return f"{sub.server_name}/{sub.tool_name}" if sub.server_name else sub.tool_name


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
        # wire name (what the model sees) → qualified name (server/tool),
        # rebuilt on each tools_for so dispatch can restore the canonical name.
        # Scoped to one resolve→dispatch cycle, matching how an agent drives its
        # own gateway: resolve once, then dispatch the calls from that turn.
        self._wire_to_qualified: dict[str, str] = {}

    def tools_for(
        self,
        subscriptions: list[ToolSubscription],
        supports_tools: bool,
    ) -> list[ToolParam]:
        """Resolve subscriptions into request ``ToolParam``s with wire-safe names.

        Definitions come from the registry (builtins + connected MCP servers)
        and from the registered adapters (e.g. the reference builtin), filtered
        to what the agent actually subscribed to. MCP tools are emitted under
        their sanitised wire name (``notion-mcp__search_pages``) and the
        ``wire→qualified`` map is rebuilt so ``dispatch`` can restore
        ``notion-mcp/search_pages``. Returns an empty list when the model can't
        call tools, so the caller passes ``tools=None`` unchanged.
        """
        self._wire_to_qualified = {}
        if not supports_tools:
            return []

        resolved: dict[str, ToolParam] = {}
        for definition in self._registry.resolve(subscriptions, supports_tools):
            self._add_tool(resolved, definition)

        # Adapter-provided definitions (e.g. the reference builtin, or an MCP
        # server's tools) matched by exact qualified name — never by bare local
        # name, so one server can't expose another's identically-named tool.
        wanted = {_subscription_qualified(sub) for sub in subscriptions}
        for adapter in self._adapters:
            for definition in adapter.provides():
                if definition.qualified_name in wanted:
                    self._add_tool(resolved, definition)
        return list(resolved.values())

    def _add_tool(self, resolved: dict[str, ToolParam], definition: ToolDefinition) -> None:
        """Register a definition under a collision-free wire name."""
        qualified = definition.qualified_name
        wire = _wire_name(qualified)
        existing = self._wire_to_qualified.get(wire)
        if existing == qualified:
            return  # already added from another source
        if existing is not None:
            # Two distinct qualified names sanitised to the same wire string;
            # disambiguate deterministically so neither is silently dropped.
            suffix = 2
            while f"{wire}_{suffix}" in self._wire_to_qualified:
                suffix += 1
            wire = f"{wire}_{suffix}"
        self._wire_to_qualified[wire] = qualified
        resolved[wire] = ToolParam(name=wire, description=definition.description, input_schema=definition.input_schema)

    async def dispatch(self, call: ToolCallResult, *, allowed: set[str]) -> ToolResult:
        """Route a tool call to its adapter and return a ``ToolResult``.

        A call outside ``allowed`` is denied without executing. Unknown names,
        bad arguments, timeouts, and adapter exceptions all come back as a
        failed ``ToolResult`` so the model can recover — never as an exception.
        """
        wire = call["function"]["name"]
        with get_tracer().start_as_current_span("tool.dispatch") as span:
            span.set_attribute("arcana.tool.name", wire)

            # Hard stop: enforce membership on the exact name the model was
            # offered before doing anything else — the agent exposes only
            # subscribed tools, and this re-check is the defense-in-depth half.
            if wire not in allowed:
                span.set_attribute("arcana.tool.denied", True)
                span.set_attribute("arcana.tool.success", False)
                return ToolResult(tool_name=wire, success=False, error="not permitted")

            # Restore the canonical server/tool name for routing and execution.
            name = self._wire_to_qualified.get(wire, wire)

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
    """A ToolGateway wired to the builtin adapter plus one adapter per MCP server.

    Registers one :class:`MCPToolAdapter` for every configured server alongside
    the builtin adapter. Adapters connect lazily, so a bad or unreachable server
    never blocks construction: its tools resolve from the persisted cache and a
    call to them fails closed with a clear error. The builtin adapter is first,
    so bare/``builtin`` names always route to it and no MCP server can shadow a
    trusted builtin.
    """
    registry = get_mcp_registry()
    adapters: list[ToolAdapter] = [BuiltinToolAdapter()]
    adapters.extend(MCPToolAdapter(server) for server in registry.list_servers())
    return ToolGateway(registry, adapters)
