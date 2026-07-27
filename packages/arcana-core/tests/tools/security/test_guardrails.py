"""Finding ``permission_bypass`` (guardrail half) — every enforced rule blocks pre-adapter.

``DENY_TOOL`` is exercised in ``test_permission``; this module covers the other
four enforced guardrail rule types, each screened in the gateway *before* an
adapter is routed to. A :class:`RecordingAdapter` proves the negative — the tool
never ran — for every rule. The fail-closed direction is asserted directly:
``REQUIRE_CONFIRMATION`` with no confirmer available *denies*, so a headless run
never self-approves.

Covers ``DENY_PATTERN``, ``SCOPE_PATHS``, ``MAX_FILE_SIZE``, ``REQUIRE_CONFIRMATION``.
"""

from pathlib import Path

import pytest

from arcana.tools.gateway import ToolGateway
from arcana.tools.guardrails import resolve_guardrails
from arcana.tools.registry import MCPRegistry
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType
from tests.support.model import tool_call
from tests.support.tools import RecordingAdapter

pytestmark = pytest.mark.security

#: Guards this module discharges — see ``security/catalog.py``.
COVERS = frozenset(
    {
        "guardrail:deny_pattern",
        "guardrail:scope_paths",
        "guardrail:max_file_size",
        "guardrail:require_confirmation",
    }
)


def _gateway(adapter: RecordingAdapter) -> ToolGateway:
    return ToolGateway(MCPRegistry(), [adapter])


async def _assert_blocked(adapter: RecordingAdapter, gateway: ToolGateway, call, guardrails) -> None:
    result = await gateway.dispatch(call, allowed={adapter.tool_name}, guardrails=guardrails)
    assert result.success is False
    assert "blocked by guardrail" in (result.error or "")
    assert adapter.executed == []  # refused before the adapter ran


async def test_deny_pattern_blocks_a_matching_command():
    adapter = RecordingAdapter("run_command")
    gateway = _gateway(adapter)
    guardrails = resolve_guardrails(
        agent_rules=[
            GuardrailRule(type=GuardrailRuleType.DENY_PATTERN, value=r"rm\s+-rf", description="no recursive rm")
        ]
    )
    call = tool_call("run_command", command="rm -rf /")
    await _assert_blocked(adapter, gateway, call, guardrails)


async def test_scope_paths_blocks_a_path_outside_the_scope(tmp_path: Path):
    adapter = RecordingAdapter("probe")
    gateway = _gateway(adapter)
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    guardrails = resolve_guardrails(
        agent_rules=[
            GuardrailRule(type=GuardrailRuleType.SCOPE_PATHS, value=[str(allowed_root)], description="scoped")
        ]
    )
    call = tool_call("probe", path=str(tmp_path / "secret.txt"))  # outside the scope
    await _assert_blocked(adapter, gateway, call, guardrails)


async def test_max_file_size_blocks_an_oversized_write():
    adapter = RecordingAdapter("write_file")
    gateway = _gateway(adapter)
    guardrails = resolve_guardrails(
        agent_rules=[GuardrailRule(type=GuardrailRuleType.MAX_FILE_SIZE, value=8, description="8-byte cap")]
    )
    call = tool_call("write_file", path="notes.md", content="x" * 64)  # over the cap
    await _assert_blocked(adapter, gateway, call, guardrails)


async def test_require_confirmation_denies_when_no_confirmer_is_available():
    """Fail-closed: an autonomous run must not silently self-approve."""
    adapter = RecordingAdapter("probe")
    gateway = _gateway(adapter)
    guardrails = resolve_guardrails(
        agent_rules=[
            GuardrailRule(type=GuardrailRuleType.REQUIRE_CONFIRMATION, value="probe", description="ask first")
        ],
        confirmer=None,  # headless
    )
    await _assert_blocked(adapter, gateway, tool_call("probe"), guardrails)
