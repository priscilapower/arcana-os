"""The security catalog's spine: the guard inventory the tier must cover.

The security tier is not "more unit tests" — it is a *named, enumerable contract*
(the same taxonomy the external adversarial harness uses) so that "what is Arcana
hardened against?" has a checkable answer in the repo. This module is the machine-
readable half of that contract: :func:`required_guards` reads the guards that
actually exist in source, and each catalog module declares the guards it covers in
a module-level ``COVERS``. ``test_presence`` asserts the union of ``COVERS`` spans
every required guard — so a guard added to source without a security test, or a
security module deleted, fails CI.

Guard identifiers are namespaced strings (``guardrail:deny_tool``,
``builtin:pathjail``, ``mcp:changed_withheld``): coarse enough to stay stable, but
derived from source where the vocabulary is a closed set the code owns — the
enforced guardrail rule types — so the inventory grows automatically when a new
enforceable rule lands.
"""

from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.guardrails import ENFORCED_RULE_TYPES
from arcana.types.tool import BuiltinTool


def _guardrail_guards() -> set[str]:
    """One guard id per *enforced* guardrail rule type, read from source.

    Derived from :data:`ENFORCED_RULE_TYPES` rather than hard-coded, so adding a
    new enforceable rule type without an adversarial test breaks the presence
    check — the "new guard, no test → CI fails" property the tier turns on.
    """
    return {f"guardrail:{rule_type.value}" for rule_type in ENFORCED_RULE_TYPES}


def _builtin_guards() -> set[str]:
    """Guard ids for the security-bearing builtins that are actually registered.

    Conditioned on the tool being present in the single-source
    :data:`BUILTIN_DEFINITIONS`: a jailed filesystem tool requires the path jail,
    ``fetch_url`` requires the egress guard, and the two execution tools each
    require their default-off gate. Removing a tool drops its requirement;
    everything shipped must carry its adversarial test.
    """
    guards: set[str] = set()
    if any(definition.path_args for definition in BUILTIN_DEFINITIONS.values()):
        guards.add("builtin:pathjail")
    if BuiltinTool.FETCH_URL in BUILTIN_DEFINITIONS:
        guards.add("builtin:egress")
    if BuiltinTool.RUN_CODE in BUILTIN_DEFINITIONS:
        guards.add("builtin:run_code_disabled")
    if BuiltinTool.RUN_COMMAND in BUILTIN_DEFINITIONS:
        guards.add("builtin:run_command_disabled")
    return guards


#: Guards not derivable from a source enum — the cross-cutting Finding classes the
#: catalog owns by name. Fixed here so a dropped catalog module still leaves the
#: requirement standing and fails the presence check rather than vanishing with it.
_CROSS_CUTTING_GUARDS = frozenset(
    {
        "gateway:membership",  # permission bypass — an unsubscribed/hallucinated tool
        "mcp:name_shadowing",  # a server claiming a trusted builtin's name
        "mcp:changed_withheld",  # a rug-pulled (mutated) tool withheld until re-approved
        "secret:no_plaintext",  # no credential in output, span, or mcps.json
    }
)


def required_guards() -> frozenset[str]:
    """Every guard the security tier must have a test for, read from source."""
    return frozenset(_guardrail_guards() | _builtin_guards() | set(_CROSS_CUTTING_GUARDS))
