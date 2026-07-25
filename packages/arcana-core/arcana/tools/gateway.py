"""ToolGateway — resolves an agent's tool subscriptions, enforces permission, and routes calls.

Sits beside ``ModelGateway``. The ``MCPRegistry`` owns tool *definitions*; the
gateway owns *execution* by mapping a tool name to the ``ToolAdapter`` that can
run it. Permission is enforced in three places (defense-in-depth): the agent only
ever exposes subscribed tools to the model, ``dispatch`` re-checks membership
before running anything, and — for an agent carrying guardrails — its rules are
evaluated before an adapter is routed to, so a blocked call never reaches the
tool at all.
"""

import asyncio
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from arcana.models.adapters.base import ToolCallResult, ToolParam
from arcana.observability import GuardrailViolationEvent, emit_guardrail_violation, get_tracer
from arcana.tools.adapters.base import BuiltinToolAdapter, ToolAdapter
from arcana.tools.adapters.mcp import MCPToolAdapter
from arcana.tools.builtins.code.config import CodeToolsConfig
from arcana.tools.builtins.fs.config import FsToolsConfig
from arcana.tools.config import DEFAULT_TOOL_TIMEOUT_S
from arcana.tools.guardrails import ActiveGuardrails, enforce, path_args_for
from arcana.tools.registry import MCPRegistry, get_mcp_registry
from arcana.types.guardrails import GuardrailRule, GuardrailViolationError
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

    async def dispatch(
        self,
        call: ToolCallResult,
        *,
        allowed: set[str],
        guardrails: ActiveGuardrails | None = None,
    ) -> ToolResult:
        """Route a tool call to its adapter and return a ``ToolResult``.

        A call outside ``allowed`` is denied without executing, and so is one a
        ``block``-severity guardrail refuses — in both cases before an adapter is
        reached, so a denied write or delete never touches the filesystem.
        Unknown names, bad arguments, timeouts, and adapter exceptions all come
        back as a failed ``ToolResult`` so the model can recover — never as an
        exception. ``guardrails`` is the set resolved once for the run; omitted,
        only membership applies.
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

            if guardrails is not None:
                blocked = await self._screen(guardrails, name, args, span)
                if blocked is not None:
                    return blocked

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

    async def _screen(
        self,
        guardrails: ActiveGuardrails,
        name: str,
        args: dict[str, Any],
        span: Any,
    ) -> ToolResult | None:
        """Evaluate the guardrail set; return the denial, or None to proceed.

        A ``block`` match is recorded and turned into a failed ``ToolResult``
        carrying the rule's own description, so the model learns *which*
        constraint it hit and can adapt rather than retry blindly. ``warn`` and
        ``log`` matches are recorded and the call continues.

        An evaluator that fails unexpectedly denies too. A guardrail that cannot
        reach a verdict must not become an implicit pass, and it must not escape
        as an exception either — the tool loop's contract is that every outcome
        is a ``ToolResult``.
        """
        try:
            observed = await enforce(guardrails, name, args)
        except GuardrailViolationError as violation:
            self._emit_violation(guardrails, name, args, violation.rule, violation.reason, blocked=True)
            span.set_attribute("arcana.tool.denied", True)
            span.set_attribute("arcana.tool.success", False)
            span.set_attribute("arcana.tool.guardrail_block", violation.rule.type.value)
            return ToolResult(
                tool_name=name,
                success=False,
                error=f"blocked by guardrail: {violation.rule.description or violation.reason}",
            )
        except Exception as exc:
            span.set_attribute("arcana.tool.denied", True)
            span.set_attribute("arcana.tool.success", False)
            span.set_attribute("arcana.tool.guardrail_block", "evaluation_error")
            return ToolResult(
                tool_name=name,
                success=False,
                error=f"blocked by guardrail: evaluation failed ({type(exc).__name__})",
            )

        for match in observed:
            self._emit_violation(guardrails, name, args, match.rule, match.reason, blocked=False)
        return None

    @staticmethod
    def _emit_violation(
        guardrails: ActiveGuardrails,
        name: str,
        args: dict[str, Any],
        rule: GuardrailRule,
        reason: str,
        *,
        blocked: bool,
    ) -> None:
        """Append a ``GuardrailViolationEvent`` for the audit trail.

        Only the tool's path arguments are carried over from ``args`` — a
        ``write_file`` violation must not spill the file's contents into the
        audit log. A two-path tool records both, since which of them tripped the
        rule is exactly what makes the entry worth reading.
        """
        supplied = [args.get(arg) for arg in path_args_for(name)]
        target = " → ".join(value for value in supplied if isinstance(value, str))
        emit_guardrail_violation(
            GuardrailViolationEvent(
                agent_id=guardrails.agent_id,
                tool_name=name,
                rule_type=rule.type.value,
                severity=rule.severity,
                reason=reason,
                blocked=blocked,
                target=target,
                description=rule.description,
            )
        )

    def _route(self, name: str) -> ToolAdapter | None:
        """First registered adapter that supports ``name``, or None."""
        return next((a for a in self._adapters if a.supports(name)), None)


def default_tool_gateway(agent_id: UUID | None = None, *, home: Path | None = None) -> ToolGateway:
    """A ToolGateway wired to the builtin adapter plus one adapter per MCP server.

    Registers one :class:`MCPToolAdapter` for every configured server alongside
    the builtin adapter. Adapters connect lazily, so a bad or unreachable server
    never blocks construction: its tools resolve from the persisted cache and a
    call to them fails closed with a clear error. The builtin adapter is first,
    so bare/``builtin`` names always route to it and no MCP server can shadow a
    trusted builtin.

    ``agent_id`` jails the filesystem builtins to that agent's workspace. Without
    it they have no allowed root and every path is refused — a gateway built with
    no agent context cannot reach the disk. ``home`` is the Arcana root that
    workspace sits under; pass it whenever the caller's root is not the default
    ``~/.arcana`` (an ``ARCANA_HOME`` override), so the jail lands beside the
    agent's own record rather than in an unrelated tree.

    ``run_code`` follows its env-backed config (``ARCANA_TOOLS_CODE_*``), which is
    disabled by default: the tool is offered but refuses to run until an operator
    turns it on and chooses a sandbox backend.
    """
    registry = get_mcp_registry()
    builtin = BuiltinToolAdapter(
        fs_config=FsToolsConfig.for_agent(agent_id, home=home),
        code_config=CodeToolsConfig(),
    )
    adapters: list[ToolAdapter] = [builtin]
    adapters.extend(MCPToolAdapter(server) for server in registry.list_servers())
    return ToolGateway(registry, adapters)
