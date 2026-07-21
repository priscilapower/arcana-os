"""Tests for web_search providers and provider selection."""

import httpx
import pytest

from arcana.tools.builtins.web.config import SearchProviderName, WebToolsConfig
from arcana.tools.builtins.web.search import (
    BraveProvider,
    DuckDuckGoProvider,
    TavilyProvider,
    make_search_provider,
)
from tests.support.tools import make_mock_client

_DDG_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=z">Example A</a>
  <a class="result__snippet" href="x">Snippet A</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Example B</a>
  <a class="result__snippet">Snippet B</a>
</div>
"""


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


# ---------------------------------------------------------------------------
# DuckDuckGo HTML parsing
# ---------------------------------------------------------------------------


def _ddg(client: httpx.AsyncClient) -> DuckDuckGoProvider:
    return DuckDuckGoProvider(client, "https://ddg.test/html/", "arcana-test/1.0")


async def test_duckduckgo_provider_decodes_redirect_urls_and_snippets():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DDG_HTML, headers={"content-type": "text/html"})

    async with make_mock_client(handler) as client:
        results = await _ddg(client).search("cats", max_results=5)
    assert results == [
        {"title": "Example A", "url": "https://example.com/a", "snippet": "Snippet A"},
        {"title": "Example B", "url": "https://example.com/b", "snippet": "Snippet B"},
    ]


async def test_duckduckgo_provider_respects_max_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DDG_HTML)

    async with make_mock_client(handler) as client:
        results = await _ddg(client).search("cats", max_results=1)
    assert len(results) == 1


async def test_duckduckgo_provider_uses_configured_url_and_user_agent():
    # The override actually reaches the wire: request hits the configured URL
    # with the configured User-Agent, not the built-in defaults.
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text=_DDG_HTML)

    async with make_mock_client(handler) as client:
        provider = DuckDuckGoProvider(client, "https://mirror.test/s/", "custom-agent/9")
        await provider.search("cats", max_results=5)

    assert seen["url"] == "https://mirror.test/s/"
    assert seen["ua"] == "custom-agent/9"


# ---------------------------------------------------------------------------
# Keyed providers
# ---------------------------------------------------------------------------


async def test_brave_provider_parses_json():
    payload = {"web": {"results": [{"title": "BT", "url": "https://b.com", "description": "BD"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-subscription-token"] == "key"
        return httpx.Response(200, json=payload)

    async with make_mock_client(handler) as client:
        results = await BraveProvider(client, "key", "https://brave.test/search").search("q", max_results=5)
    assert results == [{"title": "BT", "url": "https://b.com", "snippet": "BD"}]


async def test_tavily_provider_parses_json():
    payload = {"results": [{"title": "TT", "url": "https://t.com", "content": "TC"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with make_mock_client(handler) as client:
        results = await TavilyProvider(client, "key", "https://tavily.test/search").search("q", max_results=5)
    assert results == [{"title": "TT", "url": "https://t.com", "snippet": "TC"}]


async def test_provider_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    async with make_mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await BraveProvider(client, "key", "https://brave.test/search").search("q", max_results=5)


# ---------------------------------------------------------------------------
# make_search_provider selection
# ---------------------------------------------------------------------------


async def test_default_provider_is_duckduckgo():
    async with make_mock_client(_ok) as client:
        provider = make_search_provider(WebToolsConfig(), client)
    assert provider.name == "duckduckgo"


async def test_brave_selected_with_key():
    cfg = WebToolsConfig(web_search_provider=SearchProviderName.BRAVE, brave_api_key="k")
    async with make_mock_client(_ok) as client:
        provider = make_search_provider(cfg, client)
    assert provider.name == "brave"


async def test_keyed_provider_without_key_raises():
    async with make_mock_client(_ok) as client:
        with pytest.raises(ValueError, match="BRAVE_API_KEY"):
            make_search_provider(WebToolsConfig(web_search_provider=SearchProviderName.BRAVE), client)
        with pytest.raises(ValueError, match="TAVILY_API_KEY"):
            make_search_provider(WebToolsConfig(web_search_provider=SearchProviderName.TAVILY), client)
