"""Concurrent tool dispatch — same-server serialised, cross-adapter parallel.

When the model requests several tools in one turn, ``Agent.run`` dispatches them
together (``asyncio.gather``). Two invariants must hold under that fan-out:

* calls to the **same MCP server** are serialised — one live session, one call at
  a time — so a server that assumes ordered, non-overlapping requests is safe;
* calls to **different adapters** run in parallel — a slow builtin does not block
  an MCP call — so the fan-out is a real speed-up, not sequential in disguise.

Both are asserted structurally, not by wall-clock timing: serialisation via the
fake session's own concurrency counter, parallelism via a barrier that can only
release if the two calls genuinely overlap.
"""

import asyncio
from typing import Any

import pytest

from arcana.agents.agent import Agent
from arcana.tools.adapters.base import ToolAdapter
from arcana.tools.adapters.mcp import MCPToolAdapter
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana.types.tool import ToolDefinition, ToolResult, ToolType
from tests.support.model import ScriptedModel, calls, text, tool_call
from tests.support.tools import FakeMCPSession, connected_mcp_config, seed_mcp_registry, session_factory

pytestmark = pytest.mark.integration


class _BarrierAdapter(ToolAdapter):
    """A builtin adapter whose ``execute`` waits on a shared barrier.

    The barrier only releases once the required number of calls have arrived, so
    ``execute`` can *only* return if the calls genuinely overlapped in time —
    turning "did these run in parallel?" into a deterministic assertion (the run
    completes) instead of a wall-clock threshold that can flake under load.
    """

    type = ToolType.BUILTIN

    def __init__(self, name: str, barrier: asyncio.Barrier) -> None:
        self._name = name
        self._barrier = barrier
        self.calls = 0

    def provides(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._name, description="waits", input_schema={"type": "object"}, type=ToolType.BUILTIN
            )
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        await self._barrier.wait()  # blocks until every party has arrived
        return ToolResult(tool_name=name, success=True, output="ok")


async def test_two_calls_to_the_same_mcp_server_are_serialised():
    # No canned results → every call returns the fake's default "ok"; the delay
    # holds the per-call lock long enough for an overlap to be observable.
    session = FakeMCPSession(call_delay=0.05)
    cfg = connected_mcp_config("notion-mcp", "search_pages")
    adapter = MCPToolAdapter(cfg, session_factory=session_factory(session))
    gateway = ToolGateway(seed_mcp_registry(cfg), [adapter])

    model = ScriptedModel(
        [
            calls(
                tool_call("notion-mcp__search_pages", q="a", call_id="c1"),
                tool_call("notion-mcp__search_pages", q="b", call_id="c2"),
            ),
            text("done"),
        ]
    )
    agent = Agent(
        name="serial",
        card=Card.HERMIT,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=gateway,
        tool_subscriptions=["notion-mcp/search_pages"],
    )

    await agent.run("two at once")

    assert len(session.calls) == 2
    assert session.max_concurrent_calls == 1  # never overlapped on the one server


async def test_calls_across_adapters_run_in_parallel():
    # A 2-party barrier: neither adapter's execute() can return until *both* have
    # entered, so the run only completes if the two calls truly overlapped. If the
    # gateway serialised them, the first would wait on the barrier forever.
    barrier = asyncio.Barrier(2)
    alpha = _BarrierAdapter("alpha", barrier)
    beta = _BarrierAdapter("beta", barrier)
    gateway = ToolGateway(MCPRegistry(), [alpha, beta])

    model = ScriptedModel(
        [
            calls(
                tool_call("alpha", call_id="c1"),
                tool_call("beta", call_id="c2"),
            ),
            text("done"),
        ]
    )
    agent = Agent(
        name="parallel",
        card=Card.HERMIT,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=gateway,
        tool_subscriptions=["builtin/alpha", "builtin/beta"],
    )

    # A timeout so a serialisation regression fails fast instead of hanging the suite.
    result = await asyncio.wait_for(agent.run("both at once"), timeout=5.0)

    assert result == "done"
    assert alpha.calls == 1 and beta.calls == 1  # each ran once, concurrently past the barrier
