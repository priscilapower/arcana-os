"""Configuration for the builtin **shell-command** tool (``run_command``).

Like ``run_code``, ``run_command`` is the inverse of the other builtins': it is
**off by default**. With no configuration nothing is ever spawned; enabling it,
choosing how strongly it is isolated, and pointing it at a shell binary are all
explicit operator acts. A shell command is the most injection-shaped argument the
OS accepts, so the posture is the most conservative one the codebase has.

The switch and the resource/time/output caps ship with conservative defaults but
are **overridable via environment variables**, so an operator can enable and tune
shell execution without waiting for a release. :class:`ShellToolsTunables` is a
``pydantic-settings`` model reading ``ARCANA_TOOLS_SHELL_*`` env vars once at
import (typed, coerced, bounds-validated — a malformed value fails fast) and
seeding the module constants below. The ``_SHELL_`` segment keeps these distinct
from the code, web, and filesystem knobs under the broader ``ARCANA_TOOLS_``
prefix.

:class:`ShellToolsConfig` is the per-adapter object those constants default. The
backend, the enabled switch, and the shell binary are operator config only — a
tool argument can never set them, so a prompt-injected call cannot turn the
sandbox on, weaken it, or swap the shell.

:data:`DEFAULT_DENY_PATTERNS` is the shipped baseline for the ``DENY_PATTERN``
guardrail gate — a coarse, deliberately-incomplete tripwire over the sandbox,
never the boundary. It is a **constant**, not an env knob: operators *augment* it
through World / agent ``DENY_PATTERN`` guardrail rules rather than by editing an
env list of regexes, and the sandbox — not this blocklist — is what actually
confines a command.
"""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from arcana.tools.builtins.code.config import SandboxBackend
from arcana.types.guardrails import GuardrailRule, GuardrailRuleType

#: The conservative dangerous-command blocklist shipped with the tool, as
#: ``(regex, human description)`` pairs. Each becomes a ``block``-severity
#: ``DENY_PATTERN`` guardrail rule matched against the ``command`` string before
#: anything is spawned. This is a **tripwire and audit signal for the obvious
#: footguns**, not a safety boundary: encoding, ``$IFS``, aliases, ``eval "$VAR"``,
#: and a hundred other tricks defeat any command-string blocklist. The sandbox
#: backend is the boundary; this list only makes the loudest mistakes loud.
DEFAULT_DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    # (?i): the recursive flag is spelled -r on GNU and commonly -R on BSD/macOS,
    # so match case-insensitively — a case-sensitive `r` would sail past `rm -Rf /`.
    (r"(?i)\brm\s+-[a-z]*r[a-z]*f?\s+/", "recursive delete of a root path (rm -rf /)"),
    (r"\bmkfs\b", "filesystem format (mkfs)"),
    (r"\bdd\b.*\bof=/dev/", "raw write to a device (dd of=/dev/…)"),
    (r"\b(?:curl|wget)\b.*\|\s*(?:ba)?sh\b", "pipe-to-shell of a downloaded script (curl … | sh)"),
    (r":\s*\(\)\s*\{.*\|\s*:.*\}\s*;\s*:", "fork bomb"),
    (r"\bsudo\b", "privilege escalation (sudo)"),
    (r">\s*/dev/sd", "raw write to a disk device (> /dev/sd…)"),
)


class ShellToolsTunables(BaseSettings):
    """Shell-command knobs, overridable via ``ARCANA_TOOLS_SHELL_*`` env vars.

    Seeds :class:`ShellToolsConfig`'s field defaults. ``enabled`` is the master
    switch and defaults to ``False`` — the whole point of the slice.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_TOOLS_SHELL_", extra="ignore")

    # Master switch. Off by default: with it off, run_command returns a typed
    # error and no process is ever spawned. Turning it on is a deliberate act.
    enabled: bool = False

    # Isolation backend. The subprocess default is a *soft* sandbox — for
    # agent-authored or injection-reachable shell an operator picks bubblewrap or
    # container, which actually confine the filesystem and network.
    backend: SandboxBackend = SandboxBackend.SUBPROCESS

    # The shell binary the command runs under: ``<shell> -c <command>``. Bash by
    # default, and only bash gets the ``--noprofile --norc`` no-startup-file
    # hardening (those flags are bash-specific); a non-bash shell is the operator's
    # choice and runs with a plain ``-c``.
    shell: str = "bash"

    # Wall-clock ceiling for one command (seconds). A command past it is killed
    # via its process group and flagged ``timed_out``.
    timeout_s: float = Field(default=10.0, gt=0.0)

    # Address-space / memory ceiling for the child (MiB), enforced with setrlimit
    # on the local backends and the daemon's own limit on the container backend.
    mem_limit_mb: int = Field(default=512, gt=0)

    # Per-stream output cap (bytes). stdout and stderr are each truncated at this
    # bound and flagged ``truncated``, so a runaway command cannot flood context.
    max_output_bytes: int = Field(default=64 * 1024, gt=0)  # 64 KiB

    # Whether the child may reach the network. Off by default; best-effort on the
    # subprocess backend, enforced on bubblewrap/container. Operator config only.
    network: bool = False

    # Controlled, minimal PATH placed in the scrubbed env, so the shell resolves
    # commands from a known set of directories rather than the host's full PATH.
    path: str = "/usr/bin:/bin"

    # Container backend only: the runtime CLI and the image the command runs in.
    # The image must ship the shell the tool spawns (``bash``).
    container_command: str = "docker"
    container_image: str = "bash:5"


SHELL_TOOLS_TUNABLES = ShellToolsTunables()

DEFAULT_SHELL_ENABLED = SHELL_TOOLS_TUNABLES.enabled
DEFAULT_SHELL_BACKEND = SHELL_TOOLS_TUNABLES.backend
DEFAULT_SHELL_BINARY = SHELL_TOOLS_TUNABLES.shell
DEFAULT_SHELL_TIMEOUT_S = SHELL_TOOLS_TUNABLES.timeout_s
DEFAULT_SHELL_MEM_LIMIT_MB = SHELL_TOOLS_TUNABLES.mem_limit_mb
DEFAULT_SHELL_MAX_OUTPUT_BYTES = SHELL_TOOLS_TUNABLES.max_output_bytes
DEFAULT_SHELL_NETWORK = SHELL_TOOLS_TUNABLES.network
DEFAULT_SHELL_PATH = SHELL_TOOLS_TUNABLES.path
DEFAULT_SHELL_CONTAINER_COMMAND = SHELL_TOOLS_TUNABLES.container_command
DEFAULT_SHELL_CONTAINER_IMAGE = SHELL_TOOLS_TUNABLES.container_image


class ShellToolsConfig(BaseModel):
    """Per-adapter configuration for the builtin shell-command tool.

    Field defaults come from the env-overridable tunables above. ``enabled`` is
    default-``False`` here too, so an adapter built with no explicit config offers
    the ``run_command`` schema but refuses to run anything. ``backend``,
    ``enabled``, and ``shell`` are operator config — never influenced by a tool
    argument. ``backend``/``container_command``/``container_image`` mirror
    :class:`~arcana.tools.builtins.code.config.CodeToolsConfig` so this config is
    accepted by the shared ``make_sandbox`` factory.
    """

    enabled: bool = DEFAULT_SHELL_ENABLED
    backend: SandboxBackend = DEFAULT_SHELL_BACKEND
    shell: str = DEFAULT_SHELL_BINARY
    timeout_s: float = Field(default=DEFAULT_SHELL_TIMEOUT_S, gt=0.0)
    mem_limit_mb: int = Field(default=DEFAULT_SHELL_MEM_LIMIT_MB, gt=0)
    max_output_bytes: int = Field(default=DEFAULT_SHELL_MAX_OUTPUT_BYTES, gt=0)
    network: bool = DEFAULT_SHELL_NETWORK
    path: str = DEFAULT_SHELL_PATH
    container_command: str = DEFAULT_SHELL_CONTAINER_COMMAND
    container_image: str = DEFAULT_SHELL_CONTAINER_IMAGE

    def deny_pattern_rules(self) -> tuple[GuardrailRule, ...]:
        """The shipped :data:`DEFAULT_DENY_PATTERNS` as ``DENY_PATTERN`` rules.

        Materialized as ``block``-severity guardrail rules so the gateway seam —
        the single place ``DENY_PATTERN`` is enforced — screens a command against
        them before dispatch, emitting a ``GuardrailViolationEvent`` on a hit. The
        description travels into that audit event and the model-facing error, so
        it names the *category* of footgun, never the command body.
        """
        return tuple(
            GuardrailRule(
                type=GuardrailRuleType.DENY_PATTERN,
                value=pattern,
                description=description,
                severity="block",
            )
            for pattern, description in DEFAULT_DENY_PATTERNS
        )
