"""Tests for the guardrail enforcement seam. No LLM.

Two layers: the evaluator (does this rule refuse this call?) and the gateway
(does a refusal actually stop the adapter and land in the audit log?). The
load-bearing assertion throughout is that a blocked call never reaches an
adapter — for ``write_file`` and ``delete_file`` a late check is no check at all.
"""

from pathlib import Path
from typing import Any

import pytest

from arcana.models.adapters.base import FunctionCall, ToolCallResult
from arcana.observability import AuditLog
from arcana.tools.gateway import ToolGateway
from arcana.tools.guardrails import ActiveGuardrails, enforce, path_args_for, resolve_guardrails
from arcana.tools.registry import MCPRegistry
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType, GuardrailViolationError
from arcana.types.tool import BuiltinTool
from arcana.types.world import WorldConfig
from tests.support.tools import EchoAdapter


def _rule(rule_type: GuardrailRuleType, value: Any, **kwargs: Any) -> GuardrailRule:
    return GuardrailRule(type=rule_type, value=value, **kwargs)


def _active(*rules: GuardrailRule, confirmer: Any = None) -> ActiveGuardrails:
    return ActiveGuardrails(rules=rules, agent_id="agent-1", confirmer=confirmer)


class _Confirmer:
    """A recording stand-in for the interactive approver."""

    def __init__(self, *, approve: bool = True, raises: bool = False) -> None:
        self._approve = approve
        self._raises = raises
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def confirm(self, tool_name: str, args: dict[str, Any]) -> bool:
        self.asked.append((tool_name, args))
        if self._raises:
            raise RuntimeError("confirmer exploded")
        return self._approve


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------


def test_world_rules_come_before_agent_rules():
    world = _rule(GuardrailRuleType.DENY_TOOL, "write_file", description="world")
    agent = _rule(GuardrailRuleType.DENY_TOOL, "delete_file", description="agent")

    active = resolve_guardrails([world], [agent], agent_id="a1")

    assert [r.description for r in active.rules] == ["world", "agent"]
    assert active.agent_id == "a1"


def test_resolution_with_nothing_configured_is_empty():
    assert not resolve_guardrails()


async def test_empty_guardrails_never_refuse():
    assert await enforce(_active(), "write_file", {"path": "x", "content": "y"}) == []


# ---------------------------------------------------------------------------
# DENY_TOOL
# ---------------------------------------------------------------------------


async def test_deny_tool_blocks_a_named_tool():
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, ["builtin/write_file", "builtin/delete_file"]))

    with pytest.raises(GuardrailViolationError) as excinfo:
        await enforce(active, "write_file", {"path": "f.txt", "content": "x"})

    assert excinfo.value.tool_name == "write_file"
    assert excinfo.value.reason == "tool is denied"


async def test_deny_tool_ignores_an_unnamed_tool():
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "builtin/write_file"))
    assert await enforce(active, "read_file", {"path": "f.txt"}) == []


async def test_deny_tool_matches_with_or_without_the_builtin_prefix():
    # Rules are written like subscriptions; dispatch routes on the bare name.
    for written, dispatched in (("builtin/delete_file", "delete_file"), ("delete_file", "delete_file")):
        with pytest.raises(GuardrailViolationError):
            await enforce(_active(_rule(GuardrailRuleType.DENY_TOOL, written)), dispatched, {})


async def test_deny_tool_does_not_match_an_mcp_tool_of_the_same_bare_name():
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "builtin/delete_file"))
    assert await enforce(active, "notion-mcp/delete_file", {}) == []


# ---------------------------------------------------------------------------
# SCOPE_PATHS
# ---------------------------------------------------------------------------


async def test_scope_paths_allows_a_path_inside_the_scope(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, [str(allowed)]))

    assert await enforce(active, "read_file", {"path": str(allowed / "f.txt")}) == []


async def test_scope_paths_blocks_a_path_outside_the_scope(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, [str(allowed)]))

    with pytest.raises(GuardrailViolationError, match="outside the permitted scope"):
        await enforce(active, "read_file", {"path": str(tmp_path / "elsewhere" / "f.txt")})


async def test_scope_paths_says_nothing_about_a_call_without_a_path(tmp_path: Path):
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, [str(tmp_path)]))
    assert await enforce(active, "web_search", {"query": "arcana"}) == []


async def test_scope_paths_with_no_roots_refuses_rather_than_passing():
    # A mistyped scope must not read as "no restriction".
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, []))

    with pytest.raises(GuardrailViolationError, match="names no roots"):
        await enforce(active, "read_file", {"path": "/tmp/f.txt"})


# ---------------------------------------------------------------------------
# SCOPE_PATHS across every path argument
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["move", "copy"])
async def test_scope_paths_allows_a_two_path_call_wholly_inside_the_scope(tmp_path: Path, tool: str):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, [str(allowed)]))

    args = {"src": str(allowed / "a.txt"), "dst": str(allowed / "b.txt")}
    assert await enforce(active, tool, args) == []


@pytest.mark.parametrize("tool", ["move", "copy"])
@pytest.mark.parametrize("offending", ["src", "dst"])
async def test_scope_paths_blocks_a_two_path_call_on_either_argument(tmp_path: Path, tool: str, offending: str):
    """A scope satisfied by one argument is not a scope.

    The dangerous half is ``dst``: a ``copy`` whose source is inside the scope
    and whose destination is outside it walks the scoped data straight out, and
    a check that only ever read ``path`` would have nothing to say about it.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, [str(allowed)]))

    args = {"src": str(allowed / "a.txt"), "dst": str(allowed / "b.txt")}
    args[offending] = str(tmp_path / "elsewhere" / "f.txt")

    with pytest.raises(GuardrailViolationError, match=f"'{offending}' is outside the permitted scope"):
        await enforce(active, tool, args)


async def test_scope_paths_still_reads_path_for_a_tool_it_does_not_know(tmp_path: Path):
    # An MCP tool declares no path args because we do not own its argument
    # names. Falling back to the conventional 'path' keeps the rule enforced
    # rather than quietly narrowing its reach to builtins only.
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    active = _active(_rule(GuardrailRuleType.SCOPE_PATHS, [str(allowed)]))

    with pytest.raises(GuardrailViolationError, match="outside the permitted scope"):
        await enforce(active, "notion-mcp/export", {"path": str(tmp_path / "elsewhere")})


async def test_path_args_come_from_the_tool_definition():
    assert path_args_for("copy") == ("src", "dst")
    assert path_args_for(BuiltinTool.DELETE_DIR.qualified) == ("path",)
    assert path_args_for("some-server/some_tool") == ("path",)


# ---------------------------------------------------------------------------
# MAX_FILE_SIZE
# ---------------------------------------------------------------------------


async def test_max_file_size_blocks_an_oversize_write():
    active = _active(_rule(GuardrailRuleType.MAX_FILE_SIZE, 10))

    with pytest.raises(GuardrailViolationError, match="over the 10-byte limit"):
        await enforce(active, "write_file", {"path": "f.txt", "content": "x" * 50})


async def test_max_file_size_allows_a_write_within_the_limit():
    active = _active(_rule(GuardrailRuleType.MAX_FILE_SIZE, 10))
    assert await enforce(active, "write_file", {"path": "f.txt", "content": "short"}) == []


async def test_max_file_size_measures_encoded_bytes_not_characters():
    # "é" is one character but two UTF-8 bytes — the cap is a byte cap.
    active = _active(_rule(GuardrailRuleType.MAX_FILE_SIZE, 3))

    with pytest.raises(GuardrailViolationError):
        await enforce(active, "write_file", {"path": "f.txt", "content": "éé"})


async def test_max_file_size_with_a_non_numeric_value_refuses():
    active = _active(_rule(GuardrailRuleType.MAX_FILE_SIZE, ["not", "a", "number"]))

    with pytest.raises(GuardrailViolationError, match="not a byte count"):
        await enforce(active, "write_file", {"path": "f.txt", "content": "x"})


# ---------------------------------------------------------------------------
# REQUIRE_CONFIRMATION
# ---------------------------------------------------------------------------


async def test_require_confirmation_denies_without_a_confirmer():
    active = _active(_rule(GuardrailRuleType.REQUIRE_CONFIRMATION, "builtin/delete_file"))

    with pytest.raises(GuardrailViolationError, match="no confirmer is available"):
        await enforce(active, "delete_file", {"path": "f.txt"})


async def test_require_confirmation_allows_when_the_confirmer_approves():
    confirmer = _Confirmer(approve=True)
    active = _active(_rule(GuardrailRuleType.REQUIRE_CONFIRMATION, "builtin/delete_file"), confirmer=confirmer)

    assert await enforce(active, "delete_file", {"path": "f.txt"}) == []
    assert confirmer.asked == [("delete_file", {"path": "f.txt"})]


async def test_require_confirmation_denies_when_the_confirmer_declines():
    active = _active(
        _rule(GuardrailRuleType.REQUIRE_CONFIRMATION, "builtin/delete_file"),
        confirmer=_Confirmer(approve=False),
    )

    with pytest.raises(GuardrailViolationError, match="confirmation declined"):
        await enforce(active, "delete_file", {"path": "f.txt"})


async def test_require_confirmation_denies_when_the_confirmer_raises():
    active = _active(
        _rule(GuardrailRuleType.REQUIRE_CONFIRMATION, "builtin/delete_file"),
        confirmer=_Confirmer(raises=True),
    )

    with pytest.raises(GuardrailViolationError, match="confirmation failed"):
        await enforce(active, "delete_file", {"path": "f.txt"})


async def test_require_confirmation_ignores_unnamed_tools():
    confirmer = _Confirmer()
    active = _active(_rule(GuardrailRuleType.REQUIRE_CONFIRMATION, "builtin/delete_file"), confirmer=confirmer)

    assert await enforce(active, "read_file", {"path": "f.txt"}) == []
    assert confirmer.asked == []


# ---------------------------------------------------------------------------
# Severity and unenforced rule types
# ---------------------------------------------------------------------------


async def test_warn_severity_reports_without_blocking():
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "write_file", severity="warn"))

    observed = await enforce(active, "write_file", {"path": "f.txt", "content": "x"})

    assert [m.reason for m in observed] == ["tool is denied"]


async def test_an_unenforceable_rule_type_blocks_rather_than_passing_silently():
    active = _active(_rule(GuardrailRuleType.ALLOW_DOMAINS, ["example.com"]))

    with pytest.raises(GuardrailViolationError, match="not enforceable here"):
        await enforce(active, "fetch_url", {"url": "https://evil.test"})


async def test_the_first_blocking_rule_wins():
    active = _active(
        _rule(GuardrailRuleType.MAX_FILE_SIZE, 1, description="size"),
        _rule(GuardrailRuleType.DENY_TOOL, "write_file", description="deny"),
    )

    with pytest.raises(GuardrailViolationError) as excinfo:
        await enforce(active, "write_file", {"path": "f.txt", "content": "xxx"})

    assert excinfo.value.rule.description == "size"


# ---------------------------------------------------------------------------
# Gateway integration — the adapter must never run
# ---------------------------------------------------------------------------


def _call(name: str, arguments: str = "{}") -> ToolCallResult:
    return ToolCallResult(id="c1", type="function", function=FunctionCall(name=name, arguments=arguments))


class _SpyAdapter(EchoAdapter):
    """Echo, but recording whether it was reached at all."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    async def execute(self, name: str, args: dict[str, Any]):
        self.executed.append(name)
        return await super().execute(name, args)


def _gateway(adapter: _SpyAdapter) -> ToolGateway:
    return ToolGateway(MCPRegistry(), [adapter])


async def test_dispatch_blocks_and_never_reaches_the_adapter():
    adapter = _SpyAdapter()
    gateway = _gateway(adapter)
    gateway.tools_for([], supports_tools=True)
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "echo", description="no echoing"))

    result = await gateway.dispatch(_call("echo"), allowed={"echo"}, guardrails=active)

    assert not result.success
    assert result.error == "blocked by guardrail: no echoing"
    assert adapter.executed == []


async def test_dispatch_without_guardrails_runs_the_adapter():
    adapter = _SpyAdapter()
    gateway = _gateway(adapter)

    result = await gateway.dispatch(_call("echo", '{"message": "hi"}'), allowed={"echo"})

    assert result.success
    assert adapter.executed == ["echo"]


async def test_dispatch_with_a_warn_rule_still_runs_the_adapter():
    adapter = _SpyAdapter()
    gateway = _gateway(adapter)
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "echo", severity="warn"))

    result = await gateway.dispatch(_call("echo", '{"message": "hi"}'), allowed={"echo"}, guardrails=active)

    assert result.success
    assert adapter.executed == ["echo"]


async def test_a_block_is_recorded_in_the_audit_log(audit_log: AuditLog):
    gateway = _gateway(_SpyAdapter())
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "echo", description="no echoing"))

    await gateway.dispatch(_call("echo", '{"path": "/secret.txt"}'), allowed={"echo"}, guardrails=active)

    events = audit_log.tail(event_type="guardrail_violation")
    assert len(events) == 1
    assert events[0]["agent_id"] == "agent-1"
    assert events[0]["tool_name"] == "echo"
    assert events[0]["rule_type"] == "deny_tool"
    assert events[0]["blocked"] is True
    assert events[0]["target"] == "/secret.txt"


async def test_the_audit_event_never_carries_file_contents(audit_log: AuditLog):
    gateway = _gateway(_SpyAdapter())
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "echo"))
    secret = "SUPER-SECRET-CONTENT"

    await gateway.dispatch(
        _call("echo", f'{{"path": "/f.txt", "content": "{secret}"}}'),
        allowed={"echo"},
        guardrails=active,
    )

    events = audit_log.tail(event_type="guardrail_violation")
    assert secret not in str(events[0])


async def test_a_warn_match_is_recorded_as_not_blocked(audit_log: AuditLog):
    gateway = _gateway(_SpyAdapter())
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "echo", severity="warn"))

    await gateway.dispatch(_call("echo", '{"message": "hi"}'), allowed={"echo"}, guardrails=active)

    events = audit_log.tail(event_type="guardrail_violation")
    assert len(events) == 1
    assert events[0]["blocked"] is False


async def test_an_evaluator_failure_denies_instead_of_escaping(monkeypatch: pytest.MonkeyPatch):
    # The safety net around rule evaluation: a guardrail that cannot reach a
    # verdict must not become an implicit pass, and must not break the loop's
    # every-outcome-is-a-ToolResult contract by raising.
    adapter = _SpyAdapter()
    gateway = _gateway(adapter)

    async def _boom(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise RuntimeError("evaluator is broken")

    monkeypatch.setattr("arcana.tools.gateway.enforce", _boom)

    result = await gateway.dispatch(
        _call("echo", '{"message": "hi"}'),
        allowed={"echo"},
        guardrails=_active(_rule(GuardrailRuleType.DENY_TOOL, "something-else")),
    )

    assert not result.success
    assert result.error == "blocked by guardrail: evaluation failed (RuntimeError)"
    assert adapter.executed == []


async def test_membership_is_checked_before_guardrails():
    # An unsubscribed tool is refused on permission, not on a guardrail.
    gateway = _gateway(_SpyAdapter())
    active = _active(_rule(GuardrailRuleType.DENY_TOOL, "echo"))

    result = await gateway.dispatch(_call("echo"), allowed=set(), guardrails=active)

    assert result.error == "not permitted"


# ---------------------------------------------------------------------------
# World layering
# ---------------------------------------------------------------------------


async def test_a_world_rule_applies_to_an_agent_with_none_of_its_own():
    world = WorldConfig(system_guardrails=[_rule(GuardrailRuleType.DENY_TOOL, "delete_file")])
    active = resolve_guardrails(world.system_guardrails, [])

    with pytest.raises(GuardrailViolationError):
        await enforce(active, "delete_file", {"path": "f.txt"})
