"""Config for the code-execution builtin — the default-off posture and the knobs."""

import pytest
from pydantic import ValidationError

from arcana.tools.builtins.code.config import (
    CodeLanguage,
    CodeToolsConfig,
    CodeToolsTunables,
    SandboxBackend,
    _parse_languages,
)


def test_disabled_and_subprocess_by_default():
    # The whole slice turns on this: nothing runs unless an operator opts in.
    cfg = CodeToolsConfig()
    assert cfg.enabled is False
    assert cfg.backend is SandboxBackend.SUBPROCESS
    assert cfg.languages == [CodeLanguage.PYTHON]


def test_parse_languages_from_comma_string():
    assert _parse_languages("python,bash") == [CodeLanguage.PYTHON, CodeLanguage.BASH]
    # Whitespace and blank entries are tolerated, order is preserved.
    assert _parse_languages(" bash , python ") == [CodeLanguage.BASH, CodeLanguage.PYTHON]


def test_parse_languages_rejects_unknown_and_empty():
    with pytest.raises(ValueError):
        _parse_languages("python,cobol")
    with pytest.raises(ValueError):
        _parse_languages("  ,  ")


def test_config_coerces_a_list_of_strings():
    cfg = CodeToolsConfig(languages=["python", "bash"])
    assert cfg.languages == [CodeLanguage.PYTHON, CodeLanguage.BASH]


def test_config_rejects_an_empty_language_list():
    with pytest.raises(ValidationError):
        CodeToolsConfig(languages=[])


def test_bounds_are_validated():
    for bad in ({"timeout_s": 0}, {"mem_limit_mb": 0}, {"max_output_bytes": 0}):
        with pytest.raises(ValidationError):
            CodeToolsConfig(**bad)


def test_tunables_read_the_code_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_CODE_ENABLED", "true")
    monkeypatch.setenv("ARCANA_TOOLS_CODE_BACKEND", "bubblewrap")
    monkeypatch.setenv("ARCANA_TOOLS_CODE_LANGUAGES", "python,bash")
    monkeypatch.setenv("ARCANA_TOOLS_CODE_TIMEOUT_S", "3")

    tun = CodeToolsTunables()

    assert tun.enabled is True
    assert tun.backend is SandboxBackend.BUBBLEWRAP
    assert _parse_languages(tun.languages) == [CodeLanguage.PYTHON, CodeLanguage.BASH]
    assert tun.timeout_s == 3.0


def test_a_bad_env_language_fails_fast(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_CODE_LANGUAGES", "python,perl")
    with pytest.raises(ValidationError):
        CodeToolsTunables()
