"""Schema for the code-execution builtin (``run_code``).

This is the single source of truth for the tool's schema: it feeds the aggregated
``BUILTIN_DEFINITIONS`` (see :mod:`arcana.tools.builtins.definitions`) that both
the model-visible registry and the executing adapter consume, so the schema a
model is offered can never drift from the schema the adapter honours.

The description is deliberately honest about the posture. A model that knows the
tool is off unless enabled, that the default sandbox is *soft*, and that a
non-zero exit is a normal result — not a failure — writes better calls and
reacts to errors correctly instead of retrying blindly.
"""

from arcana.tools.builtins.code.config import CodeLanguage
from arcana.types.tool import BuiltinTool, ToolDefinition, ToolType

RUN_CODE = ToolDefinition(
    name=BuiltinTool.RUN_CODE,
    type=ToolType.BUILTIN,
    description=(
        "Execute a short program in a sandboxed subprocess and return its stdout, "
        "stderr, and exit code. Disabled by default — an operator must turn it on. "
        "The default sandbox is a *soft* isolation layer (resource and time limits, "
        "a fresh scratch workspace, and a scrubbed environment with no access to your "
        "secrets or home directory) that guards against accidents and runaway loops, "
        "not a determined attacker. A non-zero exit code comes back as a normal "
        "result, not a tool error. Output is truncated if it grows too large."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The program source to execute."},
            "language": {
                "type": "string",
                # run_code offers the program languages only; the shell-command
                # path belongs to run_command, not here.
                "enum": [CodeLanguage.PYTHON.value, CodeLanguage.BASH.value],
                "description": "Language to run the code as. Defaults to 'python'.",
            },
            "timeout_s": {
                "type": "number",
                "description": (
                    "Optional wall-clock limit in seconds. Capped at the operator's "
                    "configured maximum; a larger value is clamped down."
                ),
            },
        },
        "required": ["code"],
    },
)
