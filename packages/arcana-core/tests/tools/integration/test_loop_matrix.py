"""The loop × adapter matrix — one bounded ``Agent.run`` across every adapter type.

No single slice owns this: a run that drives a *builtin*, then an *MCP* tool, then
a *guardrail-blocked* tool — recovering from the block — in one bounded loop is the
integration test the whole gateway turns on. It is driven by the shared
:class:`ScriptedModel`, so what the model is offered and asked to call is explicit,
and it asserts the negative for the blocked cell: the denied adapter never ran.

Per-cell coverage (a lone builtin call, a lone MCP call, the iteration cap, the
no-subscription path) lives in ``tests/agents/test_agent_tools.py``; this tier owns
the *combination* those unit tests can't express.
"""

import pytest

from arcana.agents.agent import Agent
from arcana.tools.adapters.mcp import MCPToolAdapter
from arcana.tools.gateway import ToolGateway
from arcana.types.card import Card
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType
from tests.support.model import ScriptedModel, calls, text, tool_call
from tests.support.tools import (
    EchoAdapter,
    FakeMCPSession,
    RecordingAdapter,
    connected_mcp_config,
    seed_mcp_registry,
    session_factory,
    text_result,
)

pytestmark = pytest.mark.integration


def _gateway(session: FakeMCPSession, denied: RecordingAdapter) -> ToolGateway:
    """A gateway spanning all three adapter types: builtin, MCP, and a denied builtin."""
    cfg = connected_mcp_config("notion-mcp", "search_pages")
    mcp = MCPToolAdapter(cfg, session_factory=session_factory(session))
    return ToolGateway(seed_mcp_registry(cfg), [EchoAdapter(), denied, mcp])


def _agent(model: ScriptedModel, gateway: ToolGateway, *, guardrails: list[GuardrailRule] | None = None) -> Agent:
    return Agent(
        name="matrix",
        card=Card.HERMIT,
        gateway=model,  # ScriptedModel duck-types ModelGateway
        model="ollama/test-model",
        tool_gateway=gateway,
        tool_subscriptions=["builtin/echo", "builtin/probe", "notion-mcp/search_pages"],
        guardrails=guardrails,
        max_tool_iterations=6,
    )


async def test_one_run_drives_builtin_then_mcp_then_recovers_from_a_guardrail_block():
    session = FakeMCPSession(results={"search_pages": text_result("Found 3 pages")})
    denied = RecordingAdapter("probe")
    gateway = _gateway(session, denied)
    model = ScriptedModel(
        [
            calls(tool_call("echo", message="hello", call_id="c1")),  # cell 1: builtin
            calls(tool_call("notion-mcp__search_pages", q="roadmap", call_id="c2")),  # cell 2: MCP
            calls(tool_call("probe", call_id="c3")),  # cell 3: blocked by guardrail
            text("all handled"),  # recovery: final answer after the block
        ]
    )
    agent = _agent(
        model,
        gateway,
        guardrails=[GuardrailRule(type=GuardrailRuleType.DENY_TOOL, value="probe", description="probe is off-limits")],
    )

    result = await agent.run("do the thing")

    assert result == "all handled"
    assert model.completions == 4  # three tool turns + the final text
    # The builtin and MCP calls really ran; the denied one never reached its adapter.
    assert session.calls == [("search_pages", {"q": "roadmap"})]
    assert denied.executed == []

    # The session recorded all three attempts (keyed by the wire name the model
    # called), with the block as a failed result — a ``ToolCall`` is a success when
    # it carries a ``result`` and no ``error``.
    recorded = {call.tool_name: call for call in agent._sessions[-1].tool_calls}
    assert recorded["echo"].result is not None and recorded["echo"].error is None
    assert recorded["notion-mcp__search_pages"].result is not None
    assert recorded["probe"].result is None
    assert "blocked by guardrail" in (recorded["probe"].error or "")


async def test_the_model_sees_the_block_as_a_tool_error_it_can_act_on():
    """The blocked call comes back as a ``tool`` turn, so the model can adapt."""
    session = FakeMCPSession()
    denied = RecordingAdapter("probe")
    gateway = _gateway(session, denied)
    model = ScriptedModel(
        [
            calls(tool_call("probe", call_id="c1")),  # blocked immediately
            text("understood, not doing that"),
        ]
    )
    agent = _agent(
        model,
        gateway,
        guardrails=[GuardrailRule(type=GuardrailRuleType.DENY_TOOL, value="probe", description="nope")],
    )

    result = await agent.run("try the forbidden tool")

    assert result == "understood, not doing that"
    # On the second turn the model was handed the tool result — a tool-role message
    # carrying the block — as part of the history it completed against.
    second_turn = model.seen[1]
    tool_messages = [m for m in second_turn.messages if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert "blocked by guardrail" in tool_messages[0]["content"]
    assert denied.executed == []
