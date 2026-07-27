"""Finding ``permission_bypass`` — an un-permitted call never reaches its adapter.

The gateway enforces permission at dispatch as defense-in-depth: even if a call
for an unsubscribed or hallucinated tool somehow arrives, membership is re-checked
against the exact wire name the model was offered, and a ``DENY_TOOL`` guardrail is
screened, both *before* an adapter is routed to. A :class:`RecordingAdapter` proves
the negative — that ``execute`` was never called — so a block is a real block, not
a call that ran and then had its result discarded.

Covers gateway membership enforcement and the ``DENY_TOOL`` guardrail.
"""

import pytest

from arcana.tools.gateway import ToolGateway
from arcana.tools.guardrails import resolve_guardrails
from arcana.tools.registry import MCPRegistry
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType
from tests.support.model import tool_call
from tests.support.tools import RecordingAdapter

pytestmark = pytest.mark.security

#: Guards this module discharges — see ``security/catalog.py``.
COVERS = frozenset({"gateway:membership", "guardrail:deny_tool"})


def _gateway(adapter: RecordingAdapter) -> ToolGateway:
    return ToolGateway(MCPRegistry(), [adapter])


async def test_a_hallucinated_tool_is_denied_and_never_executed():
    """A call for a name the agent never subscribed to is refused pre-adapter."""
    adapter = RecordingAdapter("probe")
    gateway = _gateway(adapter)
    # ``allowed`` is what the model was actually offered — empty here.
    result = await gateway.dispatch(tool_call("probe"), allowed=set())

    assert result.success is False
    assert result.error == "not permitted"
    assert adapter.executed == []  # the adapter was never reached


async def test_an_unsubscribed_tool_is_denied_even_if_the_adapter_provides_it():
    """Providing a tool is not permission: only membership in ``allowed`` admits it."""
    adapter = RecordingAdapter("probe")
    gateway = _gateway(adapter)
    # The adapter provides ``probe``, but the agent's allowed set names a different tool.
    result = await gateway.dispatch(tool_call("probe"), allowed={"something_else"})

    assert result.success is False
    assert result.error == "not permitted"
    assert adapter.executed == []


async def test_a_deny_tool_guardrail_blocks_a_permitted_tool():
    """A subscribed tool is still refused when a ``DENY_TOOL`` rule names it."""
    adapter = RecordingAdapter("probe")
    gateway = _gateway(adapter)
    guardrails = resolve_guardrails(
        agent_rules=[
            GuardrailRule(type=GuardrailRuleType.DENY_TOOL, value="probe", description="probe is off-limits")
        ],
        agent_id="agent-1",
    )
    # ``probe`` is permitted (in ``allowed``) but a guardrail forbids it.
    result = await gateway.dispatch(tool_call("probe"), allowed={"probe"}, guardrails=guardrails)

    assert result.success is False
    assert "blocked by guardrail" in (result.error or "")
    assert adapter.executed == []  # blocked before the adapter ran
