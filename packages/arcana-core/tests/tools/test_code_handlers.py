"""The ``run_code`` handler — the fail-closed envelope around the sandbox.

These are hermetic: the disabled and validation paths never reach a sandbox, and
the "nothing spawned" guarantee is asserted by spying on the subprocess spawn
itself. The one path that does execute uses the real (local) subprocess backend.
"""

from pathlib import Path
from typing import Any

import pytest

from arcana.tools.builtins.code.config import CodeLanguage, CodeToolsConfig, SandboxBackend
from arcana.tools.builtins.code.handlers import CodeTools


def _enabled(**overrides: Any) -> CodeTools:
    return CodeTools(CodeToolsConfig(enabled=True, **overrides))


# ---------------------------------------------------------------------------
# Fail-closed: disabled, unavailable, bad arguments — nothing runs
# ---------------------------------------------------------------------------


async def test_disabled_by_default_returns_an_error(monkeypatch: pytest.MonkeyPatch):
    # NFR1: with no config the tool is off and no process is ever spawned. Spy on
    # the spawn primitive to prove the second half.
    spawned = False

    async def _spy(*_args: Any, **_kwargs: Any):
        nonlocal spawned
        spawned = True
        raise AssertionError("a disabled run_code must not spawn a process")

    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.process.asyncio.create_subprocess_exec", _spy)

    result = await CodeTools(CodeToolsConfig()).run_code({"code": "print(1)"})

    assert result.success is False
    assert result.error == "run_code is disabled"
    assert spawned is False


async def test_a_missing_binary_leaves_the_tool_disabled_with_a_reason(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.bubblewrap.shutil.which", lambda _: None)
    tools = CodeTools(CodeToolsConfig(enabled=True, backend=SandboxBackend.BUBBLEWRAP))

    result = await tools.run_code({"code": "print(1)"})

    assert result.success is False
    assert result.error is not None and "disabled" in result.error and "bwrap" in result.error


async def test_missing_code_is_refused(tmp_path: Path):
    result = await _enabled().run_code({})
    assert result.success is False
    assert result.error is not None and "code" in result.error


async def test_a_blank_code_body_is_refused():
    result = await _enabled().run_code({"code": "   "})
    assert result.success is False


async def test_an_unknown_language_is_refused_without_spawning(monkeypatch: pytest.MonkeyPatch):
    async def _spy(*_args: Any, **_kwargs: Any):
        raise AssertionError("an unsupported language must not spawn a process")

    monkeypatch.setattr("arcana.tools.builtins.code.sandbox.process.asyncio.create_subprocess_exec", _spy)

    result = await _enabled().run_code({"code": "print(1)", "language": "cobol"})

    assert result.success is False
    assert result.error is not None and "language" in result.error


async def test_a_language_outside_the_allowed_set_is_refused():
    # Python-only config: an otherwise-known language the operator did not allow
    # is still refused.
    tools = _enabled(languages=[CodeLanguage.PYTHON])
    result = await tools.run_code({"code": "echo hi", "language": "bash"})
    assert result.success is False
    assert result.error is not None and "language" in result.error


# ---------------------------------------------------------------------------
# The executing path
# ---------------------------------------------------------------------------


async def test_enabled_run_returns_the_structured_result():
    result = await _enabled().run_code({"code": "print('hi')"})
    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["stdout"].strip() == "hi"
    assert result.output["exit_code"] == 0
    assert result.output["timed_out"] is False
    assert result.output["truncated"] is False


async def test_a_model_requested_timeout_cannot_exceed_the_ceiling():
    # The model may ask for a shorter run, never a longer one: a request above the
    # operator ceiling is clamped, so a 30s request against a 0.5s cap still times
    # out fast.
    tools = _enabled(timeout_s=0.5)
    result = await tools.run_code({"code": "import time; time.sleep(30)", "timeout_s": 30})
    assert result.success is True
    assert result.output["timed_out"] is True


async def test_the_scratch_workspace_is_cleaned_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("arcana.tools.builtins.code.handlers.tempfile.mkdtemp", lambda **_: str(tmp_path / "scratch"))
    (tmp_path).mkdir(exist_ok=True)
    Path(tmp_path / "scratch").mkdir()

    await _enabled().run_code({"code": "open('artifact.txt', 'w').write('x')"})

    # The whole scratch directory (and anything the code wrote in it) is gone.
    assert not (tmp_path / "scratch").exists()
