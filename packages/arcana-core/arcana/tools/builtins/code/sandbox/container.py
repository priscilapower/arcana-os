"""``ContainerSandbox`` — the strongest isolation, in an ephemeral container.

Runs the code inside a throwaway ``--rm`` container off a pinned image, with the
scratch workspace bind-mounted as the only writable path. With ``network=False``
the container joins no network (``--network=none``); the root filesystem is
mounted ``--read-only`` with a private ``/tmp`` tmpfs, and memory is bounded by
the runtime's own ``--memory`` flag rather than ``setrlimit`` (the runtime CLI is
a thin client of a daemon, so a host-side rlimit would bound the client, not the
container).

The heaviest backend — it needs a daemon, an image pull, and seconds of spin-up —
so it is opt-in, never the default. The runtime CLI (``docker`` by default,
overridable to ``podman``) and the image are operator config. A missing runtime
binary degrades to disabled.

Timeout: each run gets a unique ``--name`` so a wall-clock timeout can stop the
container *directly* with ``{runtime} kill``. Killing the attached CLI client is
not enough — ``SIGKILL`` cannot be proxied to the container, and ``--rm`` only
removes a container once it *exits*, so without the explicit kill a timed-out or
runaway container would keep running unattended. The kill runs the container down;
``--rm`` then removes it.
"""

import asyncio
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from arcana.tools.builtins.code.config import CodeLanguage
from arcana.tools.builtins.code.sandbox.base import ExecResult, SandboxUnavailable
from arcana.tools.builtins.code.sandbox.process import interpreter_argv, run_process

#: Where the writable scratch workspace is mounted inside the container. Fixed and
#: distinct from any host path, so the code's cwd cannot imply a host location.
_CONTAINER_WORKDIR = "/workspace"

#: Prefix for the per-run container name — recognisable in ``docker ps`` and
#: distinct enough that the timeout kill never targets an unrelated container.
_NAME_PREFIX = "arcana-run-code-"

#: How long to wait for the timeout kill to take effect before giving up. The
#: container is already doomed (``--rm``); this only bounds the teardown itself.
_KILL_TIMEOUT_S = 5.0


def build_argv(
    runtime: str,
    image: str,
    inner_argv: list[str],
    *,
    name: str,
    workspace: Path,
    mem_limit_mb: int,
    network: bool,
) -> list[str]:
    """Assemble the ``docker run`` (or ``podman run``) command.

    Pure function so the isolation flags — this backend's security surface — can
    be asserted without a container runtime present. The interpreter runs from
    the image's PATH, so ``inner_argv`` uses the bare interpreter name. ``name``
    is what the timeout kill targets, so the run is stoppable by identity.
    """
    argv = [
        runtime,
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--memory",
        f"{mem_limit_mb}m",
        "--volume",
        f"{workspace}:{_CONTAINER_WORKDIR}:rw",
        "--workdir",
        _CONTAINER_WORKDIR,
    ]
    if not network:
        argv += ["--network", "none"]
    argv.append(image)
    argv += inner_argv
    return argv


def kill_command(runtime: str, name: str) -> list[str]:
    """The command that stops a running container by name on a timeout."""
    return [runtime, "kill", name]


class ContainerSandbox:
    """Ephemeral-container backend. Construct via the factory, which locates the runtime."""

    name = "container"

    def __init__(self, runtime: str, image: str) -> None:
        self._runtime = runtime
        self._image = image

    @classmethod
    def locate(cls, *, command: str, image: str) -> "ContainerSandbox":
        """Build the backend, or raise :class:`SandboxUnavailable` if the runtime is absent."""
        path = shutil.which(command)
        if path is None:
            raise SandboxUnavailable(f"container backend selected but '{command}' is not on PATH")
        return cls(path, image)

    async def run(
        self,
        code: str,
        *,
        language: CodeLanguage,
        timeout_s: float,
        mem_limit_mb: int,
        workspace: Path,
        env: dict[str, str],
        max_output_bytes: int,
        network: bool = False,
    ) -> ExecResult:
        # env is not forwarded into the container: the image's own environment is
        # the child's, and the daemon isolates it from the host either way. The
        # runtime client itself runs with the scrubbed env passed to run_process.
        name = f"{_NAME_PREFIX}{uuid.uuid4().hex}"
        inner = interpreter_argv(language, python="python")
        argv = build_argv(
            self._runtime,
            self._image,
            inner,
            name=name,
            workspace=workspace,
            mem_limit_mb=mem_limit_mb,
            network=network,
        )
        return await run_process(
            argv,
            stdin_data=code.encode("utf-8"),
            timeout_s=timeout_s,
            workspace=workspace,
            env=env,
            max_output_bytes=max_output_bytes,
            on_timeout=lambda: self._kill(name),
        )

    async def _kill(self, name: str) -> None:
        """Stop the named container directly — killing the CLI client does not.

        Best-effort and bounded: the container is already doomed by ``--rm``, so a
        kill that fails or is slow (the container already gone, the daemon
        unreachable) must not itself hang the run.
        """
        proc = await asyncio.create_subprocess_exec(
            *kill_command(self._runtime, name),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        with suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=_KILL_TIMEOUT_S)
