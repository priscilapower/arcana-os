"""Configuration for the builtin **code-execution** tool (``run_code``).

``run_code`` is the single highest-blast-radius capability the OS ships, so its
posture is the inverse of the other builtins': it is **off by default**. With no
configuration nothing is ever spawned; enabling it, and choosing how strongly it
is isolated, is an explicit operator act.

The switch and the resource/time/output caps ship with conservative defaults but
are **overridable via environment variables**, so an operator can enable and tune
execution without waiting for a release. :class:`CodeToolsTunables` is a
``pydantic-settings`` model: it reads ``ARCANA_TOOLS_CODE_*`` env vars once at
import (typed, coerced, and bounds-validated — a malformed value fails fast) and
seeds the module constants below. The ``_CODE_`` segment keeps these distinct
from the web-tool and filesystem knobs, which share the broader
``ARCANA_TOOLS_`` prefix.

:class:`CodeToolsConfig` is the per-adapter object those constants default. The
backend and the enabled switch are operator config only — a tool argument can
never set them, so a prompt-injected call cannot turn the sandbox on or weaken it.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CodeLanguage(StrEnum):
    """A language the sandbox can spawn. Python is the always-available default and
    Bash an optional second runtime for ``run_code``; ``SHELL`` is the shell-command
    path the ``run_command`` tool drives (``bash --noprofile --norc -c <command>``).

    A closed vocabulary the OS owns, so it is an enum rather than a bare string:
    it appears in the tool schema, in the per-call validation, and in the argv a
    backend builds, and a typo in any one of those must be a resolvable name, not
    a silent mismatch. ``SHELL`` is not a ``run_code`` language — that tool offers
    only ``PYTHON``/``BASH`` — but the sandbox is shared, so all three live here.
    """

    PYTHON = "python"
    BASH = "bash"
    SHELL = "shell"


class SandboxBackend(StrEnum):
    """Which isolation mechanism ``run_code`` runs behind.

    ``SUBPROCESS`` is the zero-dependency default — a *soft* jail for accidents
    and runaway loops. ``BUBBLEWRAP`` and ``CONTAINER`` are opt-in upgrades that
    enforce real filesystem and network isolation but need an external binary; a
    selection whose binary is absent degrades to "run_code disabled".
    """

    SUBPROCESS = "subprocess"
    BUBBLEWRAP = "bubblewrap"
    CONTAINER = "container"


def _parse_languages(raw: str) -> list[CodeLanguage]:
    """Parse the env comma form (``"python,bash"``) into typed languages.

    Drops blank entries and rejects an unknown name — a mistyped language must
    fail fast at config load, not silently allow nothing. Only the environment
    uses this string form; a config built in code passes a real ``list`` that
    pydantic validates natively.
    """
    languages = [CodeLanguage(token.strip()) for token in raw.split(",") if token.strip()]
    if not languages:
        raise ValueError("at least one language must be allowed")
    return languages


class CodeToolsTunables(BaseSettings):
    """Code-execution knobs, overridable via ``ARCANA_TOOLS_CODE_*`` env vars.

    Seeds :class:`CodeToolsConfig`'s field defaults. ``enabled`` is the master
    switch and defaults to ``False`` — the whole point of the slice.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_TOOLS_CODE_", extra="ignore")

    # Master switch. Off by default: with it off, run_code returns a typed error
    # and no process is ever spawned. Turning it on is a deliberate operator act.
    enabled: bool = False

    # Isolation backend. The subprocess default is a *soft* sandbox; bubblewrap
    # and container are the opt-in upgrades for actually-untrusted code.
    backend: SandboxBackend = SandboxBackend.SUBPROCESS

    # Languages the tool will run. Comma-separated in the env (``"python,bash"``);
    # an unknown name fails fast. Python only by default.
    languages: str = CodeLanguage.PYTHON.value

    # Wall-clock ceiling for one run (seconds). A run past it is killed via its
    # process group and flagged ``timed_out``.
    timeout_s: float = Field(default=10.0, gt=0.0)

    # Address-space / memory ceiling for the child (MiB), enforced with setrlimit
    # on the backends that spawn a local process, and with the daemon's own limit
    # on the container backend.
    mem_limit_mb: int = Field(default=512, gt=0)

    # Per-stream output cap (bytes). stdout and stderr are each truncated at this
    # bound and flagged ``truncated``, so a runaway ``print`` loop cannot flood
    # the model's context or the parent's memory.
    max_output_bytes: int = Field(default=64 * 1024, gt=0)  # 64 KiB

    # Whether the child may reach the network. Off by default; best-effort on the
    # subprocess backend, enforced on bubblewrap/container. Operator config only.
    network: bool = False

    # Container backend only: the runtime CLI and the image the code runs in. The
    # image must ship the interpreters the tool allows (``python``/``bash``).
    container_command: str = "docker"
    container_image: str = "python:3-slim"

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, raw: str) -> str:
        # Fail fast on a bad env value; the parsed form is produced on demand.
        _parse_languages(raw)
        return raw


CODE_TOOLS_TUNABLES = CodeToolsTunables()

DEFAULT_CODE_ENABLED = CODE_TOOLS_TUNABLES.enabled
DEFAULT_CODE_BACKEND = CODE_TOOLS_TUNABLES.backend
DEFAULT_CODE_LANGUAGES = _parse_languages(CODE_TOOLS_TUNABLES.languages)
DEFAULT_CODE_TIMEOUT_S = CODE_TOOLS_TUNABLES.timeout_s
DEFAULT_CODE_MEM_LIMIT_MB = CODE_TOOLS_TUNABLES.mem_limit_mb
DEFAULT_CODE_MAX_OUTPUT_BYTES = CODE_TOOLS_TUNABLES.max_output_bytes
DEFAULT_CODE_NETWORK = CODE_TOOLS_TUNABLES.network
DEFAULT_CODE_CONTAINER_COMMAND = CODE_TOOLS_TUNABLES.container_command
DEFAULT_CODE_CONTAINER_IMAGE = CODE_TOOLS_TUNABLES.container_image


class CodeToolsConfig(BaseModel):
    """Per-adapter configuration for the builtin code-execution tool.

    Field defaults come from the env-overridable tunables above. ``enabled`` is
    default-``False`` here too, so an adapter built with no explicit config offers
    the ``run_code`` schema but refuses to run anything. ``backend`` and
    ``enabled`` are operator config — never influenced by a tool argument.
    """

    enabled: bool = DEFAULT_CODE_ENABLED
    backend: SandboxBackend = DEFAULT_CODE_BACKEND
    languages: list[CodeLanguage] = Field(default_factory=lambda: list(DEFAULT_CODE_LANGUAGES), min_length=1)
    timeout_s: float = Field(default=DEFAULT_CODE_TIMEOUT_S, gt=0.0)
    mem_limit_mb: int = Field(default=DEFAULT_CODE_MEM_LIMIT_MB, gt=0)
    max_output_bytes: int = Field(default=DEFAULT_CODE_MAX_OUTPUT_BYTES, gt=0)
    network: bool = DEFAULT_CODE_NETWORK
    container_command: str = DEFAULT_CODE_CONTAINER_COMMAND
    container_image: str = DEFAULT_CODE_CONTAINER_IMAGE
