"""Guardrail enforcement — the pre-rejection step in front of every tool call.

:mod:`arcana.types.guardrails` defines *what* a rule is; this module decides
whether a given call violates one. It runs in the gateway before any adapter is
routed to, so a blocked call never reaches the tool: the file is not written, the
delete does not happen.

Resolution is layered and narrowing-only — the World's system rules first, then
the agent's own (themselves materialized from the card at creation) — and the
resolved set is built once per run, not once per call.

Enforced here:

* ``DENY_TOOL`` — refuse a call by qualified tool name;
* ``SCOPE_PATHS`` — the call's ``path`` must sit inside the rule's roots, which
  intersects with (never replaces) the adapter's own jail;
* ``MAX_FILE_SIZE`` — bound the bytes a write may carry;
* ``REQUIRE_CONFIRMATION`` — ask the registered confirmer, and refuse when there
  is none: an autonomous run must not silently self-approve.

A rule type this seam cannot evaluate **blocks** rather than passing. An operator
who wrote a restriction is owed enforcement or an error, never a silent no-op —
so the fail-closed direction holds for the rule set itself, not just for the
calls it screens.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from arcana.tools.builtins.fs.pathguard import PathBlocked, canonical_path, is_within
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType, GuardrailViolationError
from arcana.types.tool import BUILTIN_NAMESPACE

#: Rule types this seam evaluates. The rest of the rule vocabulary is declared
#: but not executable here, and is refused rather than ignored (see module docs).
ENFORCED_RULE_TYPES = frozenset(
    {
        GuardrailRuleType.DENY_TOOL,
        GuardrailRuleType.SCOPE_PATHS,
        GuardrailRuleType.MAX_FILE_SIZE,
        GuardrailRuleType.REQUIRE_CONFIRMATION,
    }
)

#: Tools whose byte-carrying argument ``MAX_FILE_SIZE`` bounds.
_SIZED_ARG = "content"

#: The argument every filesystem tool names its target with.
_PATH_ARG = "path"


@runtime_checkable
class ToolConfirmer(Protocol):
    """Asks a human to approve one tool call.

    The seam an interactive front-end fills in. With no confirmer registered a
    ``REQUIRE_CONFIRMATION`` rule denies outright, so a headless run fails closed
    instead of proceeding unconfirmed.
    """

    async def confirm(self, tool_name: str, args: dict[str, Any]) -> bool:
        """True to allow the call. Raising is treated as a refusal."""
        ...


@dataclass(frozen=True, slots=True)
class ActiveGuardrails:
    """The guardrail set in force for one agent, resolved once per run.

    ``agent_id`` labels the audit events a violation produces; ``confirmer`` is
    the interactive approver, absent in autonomous runs.
    """

    rules: tuple[GuardrailRule, ...] = ()
    agent_id: str = ""
    confirmer: ToolConfirmer | None = None

    def __bool__(self) -> bool:
        return bool(self.rules)


@dataclass(frozen=True, slots=True)
class GuardrailMatch:
    """A rule this call tripped, with the model-safe reason it tripped it."""

    rule: GuardrailRule
    reason: str


def resolve_guardrails(
    world_rules: Sequence[GuardrailRule] = (),
    agent_rules: Sequence[GuardrailRule] = (),
    *,
    agent_id: str = "",
    confirmer: ToolConfirmer | None = None,
) -> ActiveGuardrails:
    """Combine the World's hard floor with an agent's own rules, in that order.

    Concatenation *is* the hierarchy: rules only ever add restrictions, so a
    later layer cannot dissolve an earlier one and the World's rules stay a
    floor no agent rule can lift.
    """
    return ActiveGuardrails(
        rules=(*world_rules, *agent_rules),
        agent_id=agent_id,
        confirmer=confirmer,
    )


async def enforce(active: ActiveGuardrails, tool_name: str, args: dict[str, Any]) -> list[GuardrailMatch]:
    """Screen one call, raising on the first ``block`` and returning the rest.

    Raises :class:`GuardrailViolationError` for a blocking match — the caller
    turns it into a failed ``ToolResult`` and an audit event. ``warn`` / ``log``
    matches are returned for the caller to record; the call proceeds.
    """
    observed: list[GuardrailMatch] = []
    for rule in active.rules:
        reason = await _violation_reason(rule, tool_name, args, active.confirmer)
        if reason is None:
            continue
        if rule.severity == "block":
            raise GuardrailViolationError(rule, tool_name, reason)
        observed.append(GuardrailMatch(rule=rule, reason=reason))
    return observed


async def _violation_reason(
    rule: GuardrailRule,
    tool_name: str,
    args: dict[str, Any],
    confirmer: ToolConfirmer | None,
) -> str | None:
    """Why ``rule`` refuses this call, or None if it has nothing to say about it."""
    if rule.type not in ENFORCED_RULE_TYPES:
        return f"rule type '{rule.type.value}' is not enforceable here"

    if rule.type is GuardrailRuleType.DENY_TOOL:
        return "tool is denied" if _names_tool(rule, tool_name) else None

    if rule.type is GuardrailRuleType.SCOPE_PATHS:
        return _scope_violation(rule, args)

    if rule.type is GuardrailRuleType.MAX_FILE_SIZE:
        return _size_violation(rule, args)

    # REQUIRE_CONFIRMATION — the only rule that can consult the outside world.
    if not _names_tool(rule, tool_name):
        return None
    if confirmer is None:
        return "confirmation required but no confirmer is available"
    try:
        approved = await confirmer.confirm(tool_name, args)
    except Exception:
        return "confirmation failed"
    return None if approved else "confirmation declined"


def _names_tool(rule: GuardrailRule, tool_name: str) -> bool:
    """True if ``rule`` names ``tool_name``.

    Rules are written the way subscriptions are (``builtin/delete_file``) while
    dispatch routes on the canonical name a builtin actually carries
    (``delete_file``), so both sides drop the ``builtin/`` prefix before matching
    — an MCP tool keeps its ``server/tool`` form and stays distinct.
    """
    wanted = {_bare(value) for value in rule.values()}
    return _bare(tool_name) in wanted


def _bare(name: str) -> str:
    prefix = f"{BUILTIN_NAMESPACE}/"
    return name[len(prefix) :] if name.startswith(prefix) else name


def _scope_violation(rule: GuardrailRule, args: dict[str, Any]) -> str | None:
    """Refuse a path argument that falls outside the rule's roots.

    Silent on a call with no ``path`` — a path scope has nothing to say about a
    web search. An unusable root list refuses instead of passing, so a
    mistyped scope cannot read as "no restriction".
    """
    raw = args.get(_PATH_ARG)
    if raw is None:
        return None
    if not isinstance(raw, str):
        return "path is not a string"

    roots = [value for value in rule.values() if value.strip()]
    if not roots:
        return "path scope names no roots"

    try:
        target = canonical_path(raw)
        allowed = [canonical_path(root) for root in roots]
    except PathBlocked:
        return "path is not resolvable"

    if any(is_within(target, root) for root in allowed):
        return None
    return "path is outside the permitted scope"


def _size_violation(rule: GuardrailRule, args: dict[str, Any]) -> str | None:
    """Refuse a write whose content exceeds the rule's byte cap."""
    content = args.get(_SIZED_ARG)
    if not isinstance(content, str):
        return None

    try:
        cap = int(rule.value) if not isinstance(rule.value, list) else -1
    except (TypeError, ValueError):
        cap = -1
    if cap < 0:
        return "file-size limit is not a byte count"

    size = len(content.encode("utf-8"))
    return f"content is {size} bytes, over the {cap}-byte limit" if size > cap else None


# Re-exported so callers of the enforcement seam import the error from the same
# module as the check that raises it.
__all__ = [
    "ENFORCED_RULE_TYPES",
    "ActiveGuardrails",
    "GuardrailMatch",
    "GuardrailViolationError",
    "ToolConfirmer",
    "enforce",
    "resolve_guardrails",
]
