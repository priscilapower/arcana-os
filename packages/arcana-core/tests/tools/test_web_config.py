"""Tests for the web-tools config (ARCANA_TOOLS_* env overrides + bounds)."""

import pytest
from pydantic import ValidationError

from arcana.tools.builtins.web.config import WebToolsConfig, WebToolsTunables


def test_tunables_defaults():
    t = WebToolsTunables()
    assert t.fetch_max_bytes == 2 * 1024 * 1024
    assert t.max_redirects == 3
    assert t.allow_private_hosts is False
    assert t.web_search_provider == "duckduckgo"
    assert t.duckduckgo_search_url.startswith("https://")


def test_env_overrides_flow_to_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_FETCH_MAX_BYTES", "4096")
    monkeypatch.setenv("ARCANA_TOOLS_ALLOW_PRIVATE_HOSTS", "true")
    monkeypatch.setenv("ARCANA_TOOLS_WEB_SEARCH_USER_AGENT", "acme/1.0")
    monkeypatch.setenv("ARCANA_TOOLS_BRAVE_SEARCH_URL", "https://brave-gw.internal/search")
    t = WebToolsTunables()
    assert t.fetch_max_bytes == 4096
    assert t.allow_private_hosts is True
    assert t.web_search_user_agent == "acme/1.0"
    assert t.brave_search_url == "https://brave-gw.internal/search"


def test_config_defaults_are_sane():
    cfg = WebToolsConfig()
    assert cfg.max_connections > 0
    assert cfg.brave_api_key is None
    assert cfg.tavily_api_key is None


def test_from_env_reads_provider_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BRAVE_API_KEY", "b-key")
    monkeypatch.setenv("TAVILY_API_KEY", "t-key")
    cfg = WebToolsConfig.from_env()
    assert cfg.brave_api_key == "b-key"
    assert cfg.tavily_api_key == "t-key"


def test_out_of_bounds_value_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCANA_TOOLS_FETCH_MAX_BYTES", "0")  # must be > 0
    with pytest.raises(ValidationError):
        WebToolsTunables()
