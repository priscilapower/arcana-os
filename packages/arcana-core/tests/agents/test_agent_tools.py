"""Tests for the Agent.run tool loop."""

from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock

from arcana.agents.agent import Agent
from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    FunctionCall,
    ToolCallResult,
)
from arcana.models.gateway import ModelGateway
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from tests.support.tools import EchoAdapter


def _tool_call(name: str, arguments: str = "{}") -> ToolCallResult:
    return ToolCallResult(id="c1", type="function", function=FunctionCall(name=name, arguments=arguments))


def _tool_response(name: str, arguments: str = "{}") -> CompletionResponse:
    return CompletionResponse(content="", tool_calls=[_tool_call(name, arguments)], input_tokens=3, output_tokens=2)


def _text_response(text: str = "final answer") -> CompletionResponse:
    return CompletionResponse(content=text, input_tokens=4, output_tokens=6)


def _sequenced_gateway(responses: Sequence[CompletionResponse], *, supports_tools: bool = True) -> MagicMock:
    gateway = MagicMock(spec=ModelGateway)
    gateway.complete = AsyncMock(side_effect=list(responses))
    gateway.supports_tools = AsyncMock(return_value=supports_tools)
    return gateway


def _tool_gateway() -> ToolGateway:
    return ToolGateway(MCPRegistry(), [EchoAdapter()])


def _agent(gateway: MagicMock, *, subscriptions: list[str] | None = None, max_tool_iterations: int = 5) -> Agent:
    return Agent(
        name="tooled",
        card=Card.HERMIT,
        gateway=gateway,
        model="ollama/test-model",
        tool_gateway=_tool_gateway(),
        tool_subscriptions=subscriptions if subscriptions is not None else ["builtin/echo"],
        max_tool_iterations=max_tool_iterations,
    )


# ---------------------------------------------------------------------------
# Happy path — the loop runs a tool and re-completes
# ---------------------------------------------------------------------------


async def test_run_executes_tool_then_returns_final_text():
    gateway = _sequenced_gateway([_tool_response("echo", '{"message": "pong"}'), _text_response("done")])
    agent = _agent(gateway)

    result = await agent.run("ping the echo tool")

    assert result == "done"
    assert gateway.complete.await_count == 2


async def test_run_passes_tools_on_first_completion():
    gateway = _sequenced_gateway([_tool_response("echo", '{"message": "x"}'), _text_response()])
    agent = _agent(gateway)

    await agent.run("go")

    first_request: CompletionRequest = gateway.complete.await_args_list[0].args[1]
    assert first_request.tools is not None
    assert [t["name"] for t in first_request.tools] == ["echo"]


async def test_run_feeds_tool_result_back_into_history():
    gateway = _sequenced_gateway([_tool_response("echo", '{"message": "pong"}'), _text_response()])
    agent = _agent(gateway)

    await agent.run("go")

    second_request: CompletionRequest = gateway.complete.await_args_list[1].args[1]
    roles = [m["role"] for m in second_request.messages]
    assert "assistant" in roles and "tool" in roles
    tool_turn = next(m for m in second_request.messages if m["role"] == "tool")
    assert tool_turn["content"] == "pong"
    assert tool_turn.get("tool_call_id") == "c1"


async def test_run_records_tool_call_on_session():
    gateway = _sequenced_gateway([_tool_response("echo", '{"message": "pong"}'), _text_response()])
    agent = _agent(gateway)

    await agent.run("go")

    session = agent._sessions[-1]
    assert len(session.tool_calls) == 1
    assert session.tool_calls[0].tool_name == "echo"
    assert session.tool_calls[0].result == {"output": "pong"}


async def test_run_sums_tokens_across_iterations():
    gateway = _sequenced_gateway([_tool_response("echo", '{"message": "x"}'), _text_response()])
    agent = _agent(gateway)

    await agent.run("go")

    session = agent._sessions[-1]
    assert session.total_input_tokens == 3 + 4
    assert session.total_output_tokens == 2 + 6


# ---------------------------------------------------------------------------
# Permission — a hallucinated tool name is denied and fed back, never crashes
# ---------------------------------------------------------------------------


async def test_run_denies_unsubscribed_tool_and_recovers():
    # Model asks for web_search but only echo is subscribed.
    gateway = _sequenced_gateway([_tool_response("web_search", '{"query": "x"}'), _text_response("recovered")])
    agent = _agent(gateway, subscriptions=["builtin/echo"])

    result = await agent.run("go")

    assert result == "recovered"
    session = agent._sessions[-1]
    assert session.tool_calls[0].error == "not permitted"


# ---------------------------------------------------------------------------
# Iteration cap
# ---------------------------------------------------------------------------


async def test_run_survives_malformed_tool_arguments():
    # A model can emit non-JSON arguments; dispatch fails gracefully AND the
    # malformed call must still round-trip through history on the re-completion
    # without crashing the run.
    gateway = _sequenced_gateway([_tool_response("echo", "not-json"), _text_response("recovered")])
    agent = _agent(gateway)

    result = await agent.run("go")

    assert result == "recovered"
    session = agent._sessions[-1]
    assert session.tool_calls[0].error is not None


async def test_iteration_cap_terminates_always_calls_tools():
    always_tools = [_tool_response("echo", '{"message": "loop"}') for _ in range(10)]
    gateway = _sequenced_gateway(always_tools)
    agent = _agent(gateway, max_tool_iterations=3)

    result = await agent.run("go")

    assert gateway.complete.await_count == 3  # capped
    assert result == ""  # last response carried tool calls, no final text
    # The final (capped) completion's tools are not dispatched — only the two
    # earlier rounds ran, since their results could still be fed back.
    assert len(agent._sessions[-1].tool_calls) == 2


async def test_zero_cap_still_completes_once():
    gateway = _sequenced_gateway([_text_response("answer")])
    agent = _agent(gateway, max_tool_iterations=0)

    result = await agent.run("go")

    assert result == "answer"
    assert gateway.complete.await_count == 1


# ---------------------------------------------------------------------------
# Regression — no subscriptions behaves exactly like the tool-less path
# ---------------------------------------------------------------------------


async def test_no_subscriptions_single_completion_tools_none():
    gateway = _sequenced_gateway([_text_response("hello")])
    agent = _agent(gateway, subscriptions=[])

    result = await agent.run("go")

    assert result == "hello"
    assert gateway.complete.await_count == 1
    request: CompletionRequest = gateway.complete.await_args_list[0].args[1]
    assert request.tools is None
    gateway.supports_tools.assert_not_awaited()  # never even asked


async def test_no_tool_gateway_is_tool_less():
    gateway = _sequenced_gateway([_text_response("hi")])
    agent = Agent(
        name="plain",
        card=Card.HERMIT,
        gateway=gateway,
        model="ollama/test-model",
        tool_subscriptions=["builtin/echo"],  # subscriptions but no gateway → ignored
    )

    result = await agent.run("go")

    assert result == "hi"
    assert gateway.complete.await_count == 1
    request: CompletionRequest = gateway.complete.await_args_list[0].args[1]
    assert request.tools is None


# ---------------------------------------------------------------------------
# Capability — a model that can't call tools gets tools omitted
# ---------------------------------------------------------------------------


async def test_model_without_tool_support_omits_tools():
    gateway = _sequenced_gateway([_text_response("plain")], supports_tools=False)
    agent = _agent(gateway)

    result = await agent.run("go")

    assert result == "plain"
    assert gateway.complete.await_count == 1
    request: CompletionRequest = gateway.complete.await_args_list[0].args[1]
    assert request.tools is None
