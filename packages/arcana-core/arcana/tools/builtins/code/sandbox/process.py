"""The shared spawn-and-collect core every local-process backend runs on.

The subprocess, bubblewrap, and container backends differ only in the argv they
build; the mechanics of running it safely are identical and live here once:

* the child is its own **session leader** (``start_new_session``), so a timeout
  kills the whole *process group* — a child that forked grandchildren, or a
  fork bomb, goes down with it rather than leaking runaways;
* stdout and stderr are drained **concurrently** into fixed-size buffers, so a
  program that outpaces the reader cannot deadlock on a full pipe and cannot
  grow the parent's memory past the cap;
* the program source is fed on **stdin** and the pipe closed, so nothing the
  model wrote ever lands in the process table (an ``argv`` the way ``-c`` would);
* a wall-clock timeout cancels the readers, kills the group, and still returns
  whatever was captured before the kill — a partial, honestly-flagged result
  rather than an exception.

Everything here is POSIX: process groups, ``os.killpg``, and (via the caller's
``preexec_fn``) ``setrlimit``. That is the sandbox's floor, not a portability
statement — the backends that use it are Unix isolation tools.
"""

import asyncio
import math
import os
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from arcana.tools.builtins.code.config import CodeLanguage
from arcana.tools.builtins.code.sandbox.base import ExecResult, SandboxUnavailable

# The local-process backends need POSIX ``setrlimit`` and process groups. Guarding
# the import (rather than a bare ``import resource``) keeps the whole tools package
# importable on a non-POSIX host — ``run_code`` degrades to unavailable there,
# exactly as it does when a ``bwrap`` binary is missing, instead of breaking every
# other builtin at import time.
try:
    import resource
except ImportError:  # non-POSIX host
    resource = None

#: True on a host that provides the POSIX resource/process-group primitives the
#: subprocess and bubblewrap backends require. The factory refuses those backends
#: where it is False rather than spawning something it cannot bound.
POSIX = resource is not None

#: Read granularity when draining a child's stdout/stderr. Large enough that a
#: chatty program is drained in few syscalls, small enough that the last chunk
#: before the cap wastes little.
_READ_CHUNK_BYTES = 64 * 1024

#: CPU-second cushion over the wall-clock timeout. The wall clock is the primary
#: bound; the CPU rlimit is a faster backstop for a pure busy loop, set slightly
#: higher so it never fires before the wall clock on a normally-scheduled run.
_CPU_GRACE_SECONDS = 1

#: Cap on the bytes the code may write to any single file in its scratch
#: workspace, tied to the memory ceiling — a run cannot be given megabytes of RAM
#: yet be free to fill the disk without bound.
_FSIZE_PER_MEM_MB = 1024 * 1024


def resource_limits(timeout_s: float, mem_limit_mb: int) -> Callable[[], None]:
    """A child-side ``preexec_fn`` that applies the resource ceilings.

    Runs after the fork, before exec, so the limits are in place before the
    interpreter starts. Each limit is applied independently and a platform that
    rejects one (macOS does not always honour ``RLIMIT_AS``) still gets the rest —
    a soft sandbox degrades rather than refusing to launch. Shared by the
    subprocess and bubblewrap backends; bubblewrap namespaces isolate but do not
    bound CPU or memory, so it needs these too.
    """
    rlimit = resource
    if rlimit is None:
        # Reached only if a non-POSIX host somehow selects a local backend; the
        # factory blocks that, so this is belt-and-suspenders — apply nothing.
        return lambda: None

    cpu_seconds = max(1, math.ceil(timeout_s) + _CPU_GRACE_SECONDS)
    address_space = mem_limit_mb * 1024 * 1024
    file_size = mem_limit_mb * _FSIZE_PER_MEM_MB

    def _apply() -> None:
        for res, limit in (
            (rlimit.RLIMIT_CPU, cpu_seconds),
            (rlimit.RLIMIT_AS, address_space),
            (rlimit.RLIMIT_FSIZE, file_size),
            (rlimit.RLIMIT_CORE, 0),
        ):
            try:
                rlimit.setrlimit(res, (limit, limit))
            except (ValueError, OSError):
                # Best-effort: a limit this platform won't take is skipped, not
                # fatal — the wall-clock timeout and process-group kill still hold.
                pass

    return _apply


def interpreter_argv(language: CodeLanguage, *, python: str = "python") -> list[str]:
    """The isolated-interpreter argv that reads its program from stdin.

    ``python -I`` is isolated mode (ignores ``PYTHON*`` env vars and the user
    site dir); ``-S`` skips ``site``; ``-`` reads the script from stdin.
    ``bash --noprofile --norc -s`` starts with no startup files and reads from
    stdin. Feeding stdin (never ``-c``) keeps the model's code out of argv and
    the host process table. ``python`` is the interpreter to invoke — an absolute
    host path for the local backends, the bare name for the in-image one.
    """
    if language is CodeLanguage.PYTHON:
        return [python, "-I", "-S", "-"]
    if language is CodeLanguage.BASH:
        return ["bash", "--noprofile", "--norc", "-s"]
    raise SandboxUnavailable(f"unsupported language: {language}")


class _CappedBuffer:
    """A stream sink that keeps at most ``cap`` bytes and remembers if it had more.

    Draining continues past the cap — the excess is read and dropped rather than
    left to fill the pipe and block the child — so ``truncated`` means *the model
    saw a prefix*, not *the program was stopped*.
    """

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._chunks: list[bytes] = []
        self._kept = 0
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        room = self._cap - self._kept
        if room > 0:
            take = chunk[:room]
            self._chunks.append(take)
            self._kept += len(take)
        if len(chunk) > room:
            self.truncated = True

    def text(self) -> str:
        # errors="replace": program output is arbitrary bytes, never guaranteed
        # UTF-8, and the model must be handed text — never raw bytes, never a
        # decode error that loses the whole stream.
        return b"".join(self._chunks).decode("utf-8", errors="replace")


async def _pump(stream: asyncio.StreamReader | None, sink: _CappedBuffer) -> None:
    """Drain one stream into its capped buffer until EOF."""
    if stream is None:
        return
    while True:
        chunk = await stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            return
        sink.feed(chunk)


async def _feed_stdin(stdin: asyncio.StreamWriter | None, data: bytes) -> None:
    """Write the program source and close the pipe, tolerating an early exit.

    A program that never reads stdin, or exits before it is fully written, closes
    the pipe from its end; the resulting ``BrokenPipeError`` is expected and not a
    failure of the run.
    """
    if stdin is None:
        return
    with suppress(ConnectionResetError, BrokenPipeError):
        stdin.write(data)
        await stdin.drain()
    with suppress(ConnectionResetError, BrokenPipeError):
        stdin.close()


def _kill_group(pid: int) -> None:
    """SIGKILL the child's whole process group, best-effort.

    The child leads its own session, so the group is the child and everything it
    spawned. A group already gone (the child exited between the timeout and here)
    raises ``ProcessLookupError``, which is the desired end state anyway.
    ``AttributeError`` guards a non-POSIX host where ``killpg`` does not exist.
    """
    with suppress(ProcessLookupError, PermissionError, AttributeError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


async def run_process(
    argv: list[str],
    *,
    stdin_data: bytes,
    timeout_s: float,
    workspace: Path,
    env: dict[str, str],
    max_output_bytes: int,
    preexec_fn: Callable[[], None] | None = None,
    on_timeout: Callable[[], Awaitable[None]] | None = None,
) -> ExecResult:
    """Run ``argv``, feed ``stdin_data`` on stdin, and collect a capped result.

    Spawns the child in a fresh session with exactly ``env`` (never merged with
    the parent's), draining both streams under a single wall-clock ``timeout_s``.
    On timeout the process group is killed and the partial output returned with
    ``timed_out=True``. ``preexec_fn`` is where a backend applies ``setrlimit``;
    it runs in the child after the fork, before exec. ``on_timeout`` is an extra
    teardown a backend needs when killing the local process is not enough to stop
    the real workload — the container backend uses it to ``kill`` the container,
    since killing the CLI client alone leaves the container running.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workspace),
        env=env,
        start_new_session=True,
        preexec_fn=preexec_fn,
    )

    out, err = _CappedBuffer(max_output_bytes), _CappedBuffer(max_output_bytes)

    async def _drain_and_wait() -> int:
        # Both the stream drain and the process reap live inside the one timeout:
        # a child that closes its stdout/stderr and then hangs would give the
        # pumps an early EOF, so waiting on the process separately (outside the
        # clock) would let that child run unbounded. Kept together, the wall clock
        # bounds the whole run.
        await asyncio.gather(
            _feed_stdin(proc.stdin, stdin_data),
            _pump(proc.stdout, out),
            _pump(proc.stderr, err),
        )
        return await proc.wait()

    try:
        exit_code = await asyncio.wait_for(_drain_and_wait(), timeout=timeout_s)
        timed_out = False
    except TimeoutError:
        # wait_for has cancelled the drain; the buffers keep whatever was captured
        # before the kill. Take down the whole group, run any backend-specific
        # teardown (the container kill), then reap the direct child.
        _kill_group(proc.pid)
        if on_timeout is not None:
            with suppress(Exception):
                await on_timeout()
        try:
            exit_code = await proc.wait()
        except Exception:
            exit_code = -signal.SIGKILL
        timed_out = True

    return ExecResult(
        stdout=out.text(),
        stderr=err.text(),
        exit_code=exit_code,
        timed_out=timed_out,
        truncated=out.truncated or err.truncated,
    )
