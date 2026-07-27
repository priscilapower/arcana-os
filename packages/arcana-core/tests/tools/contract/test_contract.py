"""Contract / regression guarantees the whole gateway must not break.

Three stable promises, asserted so a drift fails loudly:

* **No-subscription runs are unchanged.** An agent with no tool subscriptions must
  complete exactly once with ``tools=None`` — the byte-for-byte-identical path the
  gateway existed before tools, so adding tools never taxes an agent that uses none.
* **The builtin schema is single-source.** What the model is *offered* for a builtin
  is the very definition the adapter *executes* against (both read
  ``BUILTIN_DEFINITIONS``), so the schema shown and the schema run can never diverge.
* **``ToolResult`` shape is stable.** The result envelope the model and the CLI read
  is pinned by a snapshot, so a field rename or drop is a conscious, reviewed change.

(The CLI ``--json`` snapshot is the arcana-cli package's own contract test; here the
core-side envelope is ``ToolResult``.)
"""

import pytest

from arcana.agents.agent import Agent
from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.gateway import ToolGateway
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana.types.tool import ToolResult, ToolSubscription
from tests.support.model import ScriptedModel, text

pytestmark = pytest.mark.contract


async def test_a_no_subscription_run_completes_once_with_no_tools():
    model = ScriptedModel([text("plain answer")])
    agent = Agent(
        name="toolless",
        card=Card.HERMIT,
        gateway=model,
        model="ollama/test-model",
        tool_gateway=ToolGateway(MCPRegistry(), []),
        tool_subscriptions=[],  # nothing subscribed
    )

    result = await agent.run("hello")

    assert result == "plain answer"
    assert model.completions == 1  # exactly one round-trip, no tool loop
    assert model.seen[0].tools is None  # the model was offered no tools


async def test_the_builtin_schema_offered_equals_what_is_executed():
    """Every builtin the adapter can execute is offered with its own source schema."""
    adapter = BuiltinToolAdapter()
    try:
        gateway = ToolGateway(MCPRegistry(), [adapter])

        # The executable set == the adapter's handler table, each carrying its
        # single-source definition.
        executable = {definition.name for definition in adapter.provides()}
        assert executable, "the builtin adapter should provide tools"
        assert executable <= set(BUILTIN_DEFINITIONS)

        # Subscribe to all of them and compare what the gateway offers the model
        # with the definition the adapter executes against.
        subscriptions = [ToolSubscription(qualified_name=f"builtin/{name}") for name in executable]
        offered = {param["name"]: param for param in gateway.tools_for(subscriptions, supports_tools=True)}

        assert set(offered) == executable  # nothing offered that can't run; nothing runnable withheld
        for name in executable:
            definition = BUILTIN_DEFINITIONS[name]
            assert offered[name]["input_schema"] == definition.input_schema
            assert offered[name]["description"] == definition.description
    finally:
        await adapter.aclose()


def test_tool_result_shape_is_stable():
    """A field rename/drop on the result envelope is a deliberate, reviewed change."""
    fields = {name: field.annotation for name, field in ToolResult.model_fields.items()}
    assert set(fields) == {"tool_name", "success", "output", "error", "duration_ms"}

    # A representative failed result serialises to the exact keys the loop and CLI read.
    dumped = ToolResult(tool_name="probe", success=False, error="not permitted").model_dump()
    assert dumped == {
        "tool_name": "probe",
        "success": False,
        "output": None,
        "error": "not permitted",
        "duration_ms": 0,
    }
