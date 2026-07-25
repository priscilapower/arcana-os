"""End-to-end: an agent writing, reading, and deleting files through the loop. No LLM.

The model is a scripted stand-in, but everything below it is real — the gateway,
the guardrail seam, the builtin adapter, and the path jail — so these cover the
seams the unit tests each see only one side of.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from arcana.agents.agent import Agent
from arcana.agents.registry import AgentRegistry
from arcana.cards.registry import CardRegistry
from arcana.models.adapters.base import CompletionResponse, FunctionCall, ToolCallResult
from arcana.models.gateway import ModelGateway
from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.fs.config import TRASH_DIR_NAME, FsToolsConfig
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType
from arcana.types.tool import BuiltinTool

_FS_SUBSCRIPTIONS = [
    BuiltinTool.LIST_DIR.qualified,
    BuiltinTool.READ_FILE.qualified,
    BuiltinTool.WRITE_FILE.qualified,
    BuiltinTool.DELETE_FILE.qualified,
    BuiltinTool.MAKE_DIR.qualified,
    BuiltinTool.MOVE.qualified,
    BuiltinTool.COPY.qualified,
    BuiltinTool.DELETE_DIR.qualified,
]


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


def _fs_gateway(workspace: Path, **fs_overrides: Any) -> tuple[ToolGateway, BuiltinToolAdapter]:
    adapter = BuiltinToolAdapter(
        WebToolsConfig(),
        fs_config=FsToolsConfig(allowed_roots=[workspace], **fs_overrides),
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
        name="filer",
        card=card,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=tool_gateway,
        tool_subscriptions=_FS_SUBSCRIPTIONS,
        guardrails=guardrails,
        confirmer=confirmer,
        max_tool_iterations=8,
    )


async def test_agent_writes_reads_then_deletes_a_file(workspace: Path):
    model = _model(
        [
            _tool_response("write_file", '{"path": "notes.md", "content": "remember this"}'),
            _tool_response("read_file", '{"path": "notes.md"}'),
            _tool_response("delete_file", '{"path": "notes.md"}'),
            _text_response("all done"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway)

    result = await agent.run("write a note, read it back, then delete it")

    assert result == "all done"
    assert not (workspace / "notes.md").exists()
    # Recoverable, not gone.
    assert len(list((workspace / TRASH_DIR_NAME).iterdir())) == 1

    calls = agent._sessions[0].tool_calls  # pyright: ignore[reportPrivateUsage]
    assert [c.tool_name for c in calls] == ["write_file", "read_file", "delete_file"]
    assert all(c.error is None for c in calls)
    await adapter.aclose()


async def test_an_agent_discovers_a_file_it_was_never_told_about(workspace: Path):
    # The gap list_dir closes: without it an agent can only read paths handed to
    # it, so a file written in an earlier session is unreachable.
    (workspace / "handover.md").write_text("findings from last time")
    model = _model(
        [
            _tool_response("list_dir", "{}"),
            _tool_response("read_file", '{"path": "handover.md"}'),
            _text_response("found it"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway)

    assert await agent.run("see what is in your workspace, then read it") == "found it"

    calls = agent._sessions[0].tool_calls  # pyright: ignore[reportPrivateUsage]
    assert [c.tool_name for c in calls] == ["list_dir", "read_file"]
    assert all(c.error is None for c in calls)

    # The listing named the file, and its contents reached the model.
    final_request = model.complete.await_args_list[2].args[1]
    tool_turns = [m.get("content") or "" for m in final_request.messages if m["role"] == "tool"]
    assert any("handover.md" in turn for turn in tool_turns)
    assert any("findings from last time" in turn for turn in tool_turns)
    await adapter.aclose()


async def test_a_hermit_may_list_since_it_is_a_read(workspace: Path):
    (workspace / "paper.md").write_text("findings")
    hermit_rules = [
        GuardrailRule(
            type=GuardrailRuleType.DENY_TOOL,
            value=[BuiltinTool.WRITE_FILE.qualified, BuiltinTool.DELETE_FILE.qualified],
        )
    ]
    model = _model([_tool_response("list_dir", "{}"), _text_response()])
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, card=Card.HERMIT, guardrails=hermit_rules)

    await agent.run("what is in your workspace?")

    assert agent._sessions[0].tool_calls[0].error is None  # pyright: ignore[reportPrivateUsage]
    await adapter.aclose()


async def test_the_read_back_content_reaches_the_model(workspace: Path):
    model = _model(
        [
            _tool_response("write_file", '{"path": "notes.md", "content": "remember this"}'),
            _tool_response("read_file", '{"path": "notes.md"}'),
            _text_response(),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)

    await _agent(model, gateway).run("round-trip a file")

    # The third completion carries the tool turns from the first two.
    final_request = model.complete.await_args_list[2].args[1]
    tool_turns = [m for m in final_request.messages if m["role"] == "tool"]
    assert any("remember this" in (m.get("content") or "") for m in tool_turns)
    await adapter.aclose()


async def test_a_traversal_attempt_fails_without_touching_the_filesystem(workspace: Path, tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    model = _model(
        [
            _tool_response("read_file", '{"path": "../secret.txt"}'),
            _text_response("could not read it"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway)

    await agent.run("read the secret")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error == "blocked: path outside allowed roots"
    assert secret.read_text() == "classified"
    await adapter.aclose()


async def test_a_hermit_is_denied_write_and_delete_end_to_end(workspace: Path):
    hermit_rules = [
        GuardrailRule(
            type=GuardrailRuleType.DENY_TOOL,
            value=[BuiltinTool.WRITE_FILE.qualified, BuiltinTool.DELETE_FILE.qualified],
            description="The Hermit observes and reads; it does not alter the world.",
        )
    ]
    model = _model(
        [
            _tool_response("write_file", '{"path": "notes.md", "content": "x"}'),
            _text_response("I cannot write"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, card=Card.HERMIT, guardrails=hermit_rules)

    await agent.run("write a note")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    assert not (workspace / "notes.md").exists()
    await adapter.aclose()


async def test_a_hermit_may_still_read(workspace: Path):
    (workspace / "paper.md").write_text("findings")
    hermit_rules = [
        GuardrailRule(
            type=GuardrailRuleType.DENY_TOOL,
            value=[BuiltinTool.WRITE_FILE.qualified, BuiltinTool.DELETE_FILE.qualified],
        )
    ]
    model = _model([_tool_response("read_file", '{"path": "paper.md"}'), _text_response()])
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, card=Card.HERMIT, guardrails=hermit_rules)

    await agent.run("read the paper")

    assert agent._sessions[0].tool_calls[0].error is None  # pyright: ignore[reportPrivateUsage]
    await adapter.aclose()


async def test_require_confirmation_denies_a_delete_in_an_autonomous_run(workspace: Path):
    (workspace / "doomed.txt").write_text("still here")
    rules = [
        GuardrailRule(
            type=GuardrailRuleType.REQUIRE_CONFIRMATION,
            value=BuiltinTool.DELETE_FILE.qualified,
            description="Deletions require explicit user confirmation.",
        )
    ]
    model = _model([_tool_response("delete_file", '{"path": "doomed.txt"}'), _text_response()])
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, guardrails=rules)  # no confirmer — headless

    await agent.run("delete it")

    assert (workspace / "doomed.txt").read_text() == "still here"
    await adapter.aclose()


async def test_require_confirmation_allows_the_delete_with_a_confirmer(workspace: Path):
    (workspace / "doomed.txt").write_text("bye")
    rules = [GuardrailRule(type=GuardrailRuleType.REQUIRE_CONFIRMATION, value=BuiltinTool.DELETE_FILE.qualified)]

    class _Approver:
        async def confirm(self, tool_name: str, args: dict[str, Any]) -> bool:
            return True

    model = _model([_tool_response("delete_file", '{"path": "doomed.txt"}'), _text_response()])
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, guardrails=rules, confirmer=_Approver())

    await agent.run("delete it")

    assert not (workspace / "doomed.txt").exists()
    await adapter.aclose()


async def test_an_agent_organises_a_workspace_end_to_end(workspace: Path):
    # The whole directory story in one run: make a folder, put a file in it
    # (Slice 5's write), duplicate the folder, then recursively remove the copy.
    model = _model(
        [
            _tool_response("make_dir", '{"path": "project"}'),
            _tool_response("write_file", '{"path": "project/notes.md", "content": "findings"}'),
            _tool_response("copy", '{"src": "project", "dst": "project-backup"}'),
            _tool_response("delete_dir", '{"path": "project-backup"}'),
            _text_response("organised"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway)

    assert await agent.run("set up a project folder, back it up, then drop the backup") == "organised"

    calls = agent._sessions[0].tool_calls  # pyright: ignore[reportPrivateUsage]
    assert [c.tool_name for c in calls] == ["make_dir", "write_file", "copy", "delete_dir"]
    assert all(c.error is None for c in calls)

    assert (workspace / "project" / "notes.md").read_text() == "findings"
    assert not (workspace / "project-backup").exists()
    # The backup is recoverable, not gone: the whole tree is one trash entry.
    trashed = list((workspace / TRASH_DIR_NAME).iterdir())
    assert len(trashed) == 1
    assert (trashed[0] / "notes.md").read_text() == "findings"
    await adapter.aclose()


async def test_an_agent_cannot_copy_data_out_of_its_scope(workspace: Path, tmp_path: Path):
    # The two-path failure a single-path check would miss: the source is inside
    # the scope and the destination is not, so the call has to be refused on dst.
    inner = workspace / "inner"
    inner.mkdir()
    (inner / "secret.md").write_text("scoped data")
    rules = [GuardrailRule(type=GuardrailRuleType.SCOPE_PATHS, value=[str(inner)])]
    model = _model(
        [
            _tool_response("copy", f'{{"src": "inner/secret.md", "dst": "{workspace}/leaked.md"}}'),
            _text_response("could not copy it"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, guardrails=rules)

    await agent.run("copy the secret out")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    assert not (workspace / "leaked.md").exists()
    await adapter.aclose()


async def test_a_hermit_is_denied_every_mutating_directory_tool(workspace: Path):
    _tree = workspace / "tree"
    _tree.mkdir()
    hermit = CardRegistry().get(Card.HERMIT)
    model = _model(
        [
            _tool_response("delete_dir", '{"path": "tree"}'),
            _text_response("I cannot remove it"),
        ]
    )
    gateway, adapter = _fs_gateway(workspace)
    agent = _agent(model, gateway, card=Card.HERMIT, guardrails=list(hermit.archetype.default_guardrails))

    await agent.run("delete the tree")

    call = agent._sessions[0].tool_calls[0]  # pyright: ignore[reportPrivateUsage]
    assert call.error is not None
    assert "blocked by guardrail" in call.error
    assert _tree.is_dir()
    await adapter.aclose()


def test_the_default_jail_follows_the_registry_root_not_the_real_home(tmp_path: Path):
    """A registry pointed at an ARCANA_HOME override must jail there, not in ~/.arcana."""
    registry = AgentRegistry(tmp_path / "custom-home" / "agents")
    record = registry.create(name="filer", card=Card.MAGICIAN, model="ollama/test-model")

    agent = registry.build_runtime(record, MagicMock(spec=ModelGateway))

    builtin = agent._tool_gateway._adapters[0]  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    roots = builtin._fs._guard.roots  # pyright: ignore[reportPrivateUsage]
    assert roots == [tmp_path / "custom-home" / "agents" / str(record.id) / "workspace"]


async def test_an_agent_without_fs_subscriptions_is_unaffected(workspace: Path):
    # Regression: the guardrail seam must not change the tool-less path.
    model = _model([_text_response("plain answer")])
    gateway, adapter = _fs_gateway(workspace)
    agent = Agent(
        name="plain",
        card=Card.FOOL,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=gateway,
    )

    assert await agent.run("hello") == "plain answer"
    assert model.complete.await_args_list[0].args[1].tools is None
    await adapter.aclose()
