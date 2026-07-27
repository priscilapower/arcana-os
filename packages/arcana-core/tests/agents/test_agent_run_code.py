"""End-to-end: an agent executing code through the loop, and the policy that gates it.

The model is scripted, but the gateway, the guardrail seam, the builtin adapter,
and the real subprocess sandbox below it are all live — so these cover the seam
where policy (deny / confirm) does the safety work *before* the sandbox is ever
reached, plus the one path where it does run.
"""

from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcana.agents.agent import Agent
from arcana.cards.registry import CardRegistry
from arcana.models.adapters.base import CompletionResponse, FunctionCall, ToolCallResult
from arcana.models.gateway import ModelGateway
from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.code.config import CodeToolsConfig
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType
from arcana.types.tool import BuiltinTool

_RUN_CODE = BuiltinTool.RUN_CODE.qualified


def _tool_call(name: str, arguments: str) -> ToolCallResult:
    return ToolCallResult(id=f"c-{name}", type="function", function=FunctionCall(name=name, arguments=arguments))


def _tool_response(name: str, arguments: str) -> CompletionResponse:
    return CompletionResponse(content="", tool_calls=[_tool_call(name, arguments)], input_tokens=3, output_tokens=2)


def _text_response(text: str = "done") -> CompletionResponse:
    return CompletionResponse(content=text, input_tokens=4, output_tokens=6)


def _model(responses: Sequence[CompletionResponse]) -> MagicMock:
    gateway = MagicMock(spec=ModelGateway)
    gateway.complete = AsyncMock(side_effect=list(responses))
    gateway.supports_tools = AsyncMock(return_value=True)
    return gateway


def _code_gateway(*, enabled: bool = True) -> tuple[ToolGateway, BuiltinToolAdapter]:
    adapter = BuiltinToolAdapter(WebToolsConfig(), code_config=CodeToolsConfig(enabled=enabled))
    return ToolGateway(MCPRegistry(), [adapter]), adapter


def _agent(
    model: MagicMock,
    tool_gateway: ToolGateway,
    *,
    card: Card = Card.MAGICIAN,
    guardrails: list[GuardrailRule] | None = None,
    confirmer: Any = None,
) -> Agent:
    return Agent(
        name="coder",
        card=card,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=tool_gateway,
        tool_subscriptions=[_RUN_CODE],
        guardrails=guardrails,
        confirmer=confirmer,
        max_tool_iterations=4,
    )


@pytest.mark.slow
async def test_an_agent_runs_code_and_uses_the_result():
    model = _model(
        [
            _tool_response("run_code", '{"code": "print(2 + 2)"}'),
            _text_response("the answer is 4"),
        ]
    )
    gateway, adapter = _code_gateway(enabled=True)
    # No guardrails here: this is the execute path, gated elsewhere by policy.
    agent = _agent(model, gateway, guardrails=[])

    assert await agent.run("compute two plus two") == "the answer is 4"

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.tool_name == "run_code"
    assert call.error is None

    # The stdout reached the model on the follow-up turn.
    final_request = model.complete.await_args_list[1].args[1]
    tool_turns = [m.get("content") or "" for m in final_request.messages if m["role"] == "tool"]
    assert any("4" in turn for turn in tool_turns)
    await adapter.aclose()


async def test_run_code_disabled_is_a_clean_error_through_the_loop():
    model = _model([_tool_response("run_code", '{"code": "print(1)"}'), _text_response("cannot run")])
    gateway, adapter = _code_gateway(enabled=False)
    agent = _agent(model, gateway, guardrails=[])

    await agent.run("run something")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None and "disabled" in call.error
    await adapter.aclose()


async def test_the_hermit_is_denied_run_code():
    hermit = CardRegistry().get(Card.HERMIT)
    model = _model([_tool_response("run_code", '{"code": "print(1)"}'), _text_response("I do not execute")])
    gateway, adapter = _code_gateway(enabled=True)
    agent = _agent(model, gateway, card=Card.HERMIT, guardrails=list(hermit.archetype.default_guardrails))

    await agent.run("run some code")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    await adapter.aclose()


async def test_require_confirmation_denies_run_code_in_an_autonomous_run():
    # The Magician may execute, but its default guardrail gates run_code behind
    # confirmation — and a headless run has no confirmer, so it fails closed.
    magician = CardRegistry().get(Card.MAGICIAN)
    model = _model([_tool_response("run_code", '{"code": "print(1)"}'), _text_response("need confirmation")])
    gateway, adapter = _code_gateway(enabled=True)
    agent = _agent(model, gateway, guardrails=list(magician.archetype.default_guardrails))  # no confirmer

    await agent.run("run some code")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    await adapter.aclose()


@pytest.mark.slow
async def test_require_confirmation_allows_run_code_with_a_confirmer():
    rules = [GuardrailRule(type=GuardrailRuleType.REQUIRE_CONFIRMATION, value=_RUN_CODE)]

    class _Approver:
        async def confirm(self, tool_name: str, args: dict[str, Any]) -> bool:
            return True

    model = _model([_tool_response("run_code", '{"code": "print(1)"}'), _text_response("ran it")])
    gateway, adapter = _code_gateway(enabled=True)
    agent = _agent(model, gateway, guardrails=rules, confirmer=_Approver())

    await agent.run("run some code")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is None
    assert call.tool_name == "run_code"
    await adapter.aclose()
