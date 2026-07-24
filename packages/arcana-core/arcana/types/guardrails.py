"""Guardrail rules — the declarative constraints on what tools an agent may call.

A guardrail is a **static, serializable rule**, not a callable: it survives a
process restart, diffs cleanly in an ``agent.json``, and can be inspected without
being run. Rules layer in one direction only — narrowing, never widening:

* ``World.system_guardrails`` — the operator's hard floor, applied to every agent.
* ``Agent.guardrails`` — instance rules, materialized from the card at creation.
* ``CardArchetype.default_guardrails`` — archetype defaults (The Hermit reads but
  does not alter the world).

Enforcement lives in the tool gateway, which pre-rejects a violating call so the
tool never runs — see :mod:`arcana.tools.guardrails`. These types are the shape
of the rules only; they carry no evaluation logic, so any surface can persist,
render, or reason about an agent's constraints without importing the executor.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

#: What a matched rule does. ``block`` denies the call, ``warn`` and ``log`` are
#: recorded and let it through — the difference is only how loudly they surface.
GuardrailSeverity = Literal["block", "warn", "log"]


class GuardrailRuleType(StrEnum):
    """The constraint categories a guardrail rule can express.

    ``value`` is interpreted per type: a tool name or list of them for
    ``DENY_TOOL`` / ``REQUIRE_CONFIRMATION``, a list of filesystem roots for
    ``SCOPE_PATHS``, a byte count for ``MAX_FILE_SIZE``, a regular expression for
    ``DENY_PATTERN``, and a list of hostnames for ``ALLOW_DOMAINS``.
    """

    DENY_TOOL = "deny_tool"
    DENY_PATTERN = "deny_pattern"
    ALLOW_DOMAINS = "allow_domains"
    SCOPE_PATHS = "scope_paths"
    MAX_FILE_SIZE = "max_file_size"
    REQUIRE_CONFIRMATION = "require_confirmation"


class GuardrailRule(BaseModel):
    """One constraint on tool use, stored on a card, an agent, or the World."""

    type: GuardrailRuleType
    value: str | int | list[str]
    description: str = ""
    severity: GuardrailSeverity = "block"

    def values(self) -> list[str]:
        """``value`` as a list of strings, whatever shape it was written in.

        A rule naming a single tool (``"builtin/delete_file"``) and one naming
        several read the same to a caller, so the evaluator never has to branch
        on the union.
        """
        if isinstance(self.value, list):
            return self.value
        return [str(self.value)]


class GuardrailViolationError(Exception):
    """A tool call was refused by a ``block``-severity rule.

    Raised by the evaluator before any adapter runs and converted by the gateway
    into a failed ``ToolResult``, so the model sees a specific, adaptable error
    rather than an exception. ``reason`` is model-safe: it names the rule and
    what tripped it, never the file contents that tripped it.
    """

    def __init__(self, rule: GuardrailRule, tool_name: str, reason: str) -> None:
        super().__init__(reason)
        self.rule = rule
        self.tool_name = tool_name
        self.reason = reason
