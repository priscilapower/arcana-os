"""Schema for the shell-command builtin (``run_command``).

The single source of truth for the tool's schema: it feeds the aggregated
``BUILTIN_DEFINITIONS`` that both the model-visible registry and the executing
adapter consume, so the schema a model is offered can never drift from the one
the adapter honours.

The description is deliberately honest about the posture. A model that knows the
tool is off unless enabled, that the default sandbox is *soft* and unsuitable for
untrusted shell, that a dangerous-command blocklist is coarse and incomplete, and
that a non-zero exit is a normal result — not a failure — writes better calls and
reacts to errors correctly instead of retrying blindly.
"""

from arcana.types.tool import BuiltinTool, ToolDefinition, ToolType

RUN_COMMAND = ToolDefinition(
    name=BuiltinTool.RUN_COMMAND,
    type=ToolType.BUILTIN,
    description=(
        "Run a shell command (e.g. 'git status | head', 'grep -r TODO .', 'ls') in a "
        "sandboxed subprocess and return its stdout, stderr, and exit code. Disabled by "
        "default — an operator must turn it on. The command runs in the agent's workspace "
        "as the working directory. The default sandbox is a *soft* isolation layer "
        "(resource and time limits, a scrubbed environment with no access to your secrets "
        "or home directory) that guards against accidents and runaway commands, not a "
        "determined attacker — untrusted shell should run under the bubblewrap or container "
        "backend, which is the real boundary. A conservative blocklist rejects a few obvious "
        "footguns (rm -rf /, curl|sh, sudo) but is coarse and incomplete, never the "
        "boundary. A non-zero exit code comes back as a normal result, not a tool error. "
        "Output is truncated if it grows too large. One-shot only: no interactive, "
        "long-running, or streaming commands."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout_s": {
                "type": "number",
                "description": (
                    "Optional wall-clock limit in seconds. Capped at the operator's "
                    "configured maximum; a larger value is clamped down."
                ),
            },
        },
        "required": ["command"],
    },
)
