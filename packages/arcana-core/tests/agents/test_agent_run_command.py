"""End-to-end: an agent running a shell command through the loop, and its policy.

The model is scripted, but the gateway, the guardrail seam (including the shipped
``DENY_PATTERN`` baseline), the builtin adapter, and the real subprocess sandbox
below it are all live — so these cover the seam where policy (deny / confirm /
pattern) does the safety work *before* the sandbox is reached, plus the one path
where it does run in the agent's workspace.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcana.agents.agent import Agent
from arcana.cards.registry import CardRegistry
from arcana.models.adapters.base import CompletionResponse, FunctionCall, ToolCallResult
from arcana.models.gateway import ModelGateway
from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.shell.config import ShellToolsConfig
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana.types.guardrails import GuardrailRule
from arcana.types.tool import BuiltinTool

_RUN_COMMAND = BuiltinTool.RUN_COMMAND.qualified


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


def _shell_gateway(workspace: Path, *, enabled: bool = True) -> tuple[ToolGateway, BuiltinToolAdapter]:
    adapter = BuiltinToolAdapter(
        WebToolsConfig(),
        shell_config=ShellToolsConfig(enabled=enabled),
        agent_workspace=workspace,
    )
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
        name="operator",
        card=card,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=tool_gateway,
        tool_subscriptions=[_RUN_COMMAND],
        guardrails=guardrails,
        confirmer=confirmer,
        max_tool_iterations=4,
    )


@pytest.mark.slow
async def test_an_agent_runs_a_command_and_uses_the_result(tmp_path: Path):
    model = _model(
        [
            _tool_response("run_command", '{"command": "echo 42"}'),
            _text_response("the answer is 42"),
        ]
    )
    gateway, adapter = _shell_gateway(tmp_path, enabled=True)
    # No guardrails here beyond the shipped baseline: this is the execute path.
    agent = _agent(model, gateway, guardrails=[])

    assert await agent.run("print the answer") == "the answer is 42"

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.tool_name == "run_command"
    assert call.error is None

    # The stdout reached the model on the follow-up turn.
    final_request = model.complete.await_args_list[1].args[1]
    tool_turns = [m.get("content") or "" for m in final_request.messages if m["role"] == "tool"]
    assert any("42" in turn for turn in tool_turns)
    await adapter.aclose()


@pytest.mark.slow
async def test_run_command_reads_a_file_the_agent_wrote(tmp_path: Path):
    # The command shares the agent workspace, so it can operate on files staged
    # there — grep over a file written into the same jailed directory.
    (tmp_path / "notes.txt").write_text("alpha\nTODO beta\ngamma\n")
    model = _model(
        [
            _tool_response("run_command", '{"command": "grep TODO notes.txt"}'),
            _text_response("found the todo"),
        ]
    )
    gateway, adapter = _shell_gateway(tmp_path, enabled=True)
    agent = _agent(model, gateway, guardrails=[])

    await agent.run("find the todo")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is None
    final_request = model.complete.await_args_list[1].args[1]
    tool_turns = [m.get("content") or "" for m in final_request.messages if m["role"] == "tool"]
    assert any("beta" in turn for turn in tool_turns)
    await adapter.aclose()


async def test_run_command_disabled_is_a_clean_error_through_the_loop(tmp_path: Path):
    model = _model([_tool_response("run_command", '{"command": "echo hi"}'), _text_response("cannot run")])
    gateway, adapter = _shell_gateway(tmp_path, enabled=False)
    agent = _agent(model, gateway, guardrails=[])

    await agent.run("run something")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None and "disabled" in call.error
    await adapter.aclose()


async def test_a_dangerous_command_is_blocked_by_the_shipped_blocklist(tmp_path: Path):
    # The DEFAULT_DENY_PATTERNS baseline fires in the seam even for an agent with
    # no guardrails of its own: a sudo command never reaches the sandbox.
    model = _model([_tool_response("run_command", '{"command": "sudo rm -rf /"}'), _text_response("refused")])
    gateway, adapter = _shell_gateway(tmp_path, enabled=True)
    agent = _agent(model, gateway, guardrails=[])

    await agent.run("nuke it")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    await adapter.aclose()


async def test_the_hermit_is_denied_run_command(tmp_path: Path):
    hermit = CardRegistry().get(Card.HERMIT)
    model = _model([_tool_response("run_command", '{"command": "echo hi"}'), _text_response("I do not execute")])
    gateway, adapter = _shell_gateway(tmp_path, enabled=True)
    agent = _agent(model, gateway, card=Card.HERMIT, guardrails=list(hermit.archetype.default_guardrails))

    await agent.run("run a command")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    await adapter.aclose()


async def test_require_confirmation_denies_run_command_in_an_autonomous_run(tmp_path: Path):
    # The Magician may execute, but its default guardrail gates run_command behind
    # confirmation — and a headless run has no confirmer, so it fails closed.
    magician = CardRegistry().get(Card.MAGICIAN)
    model = _model([_tool_response("run_command", '{"command": "echo hi"}'), _text_response("need confirmation")])
    gateway, adapter = _shell_gateway(tmp_path, enabled=True)
    agent = _agent(model, gateway, guardrails=list(magician.archetype.default_guardrails))  # no confirmer

    await agent.run("run a command")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    await adapter.aclose()
