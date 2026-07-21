"""``web_search`` providers behind a swappable seam.

The default is **DuckDuckGo** — no API key, so ``pip install arcana-os`` yields a
working agent with no signup; **Brave** and **Tavily** are strictly better and
opt-in via config plus a key in the environment. ``make_search_provider`` selects
one from :class:`WebToolsConfig`; a keyed provider without its key is a
configuration error raised here (at adapter construction) rather than surfacing as
a silent empty result.
"""

import httpx

from arcana.tools.builtins.web.config import SearchProviderName, WebToolsConfig
from arcana.tools.builtins.web.search.base import SearchProvider, SearchResult
from arcana.tools.builtins.web.search.brave import BraveProvider
from arcana.tools.builtins.web.search.duckduckgo import DuckDuckGoProvider
from arcana.tools.builtins.web.search.tavily import TavilyProvider

__all__ = [
    "BraveProvider",
    "DuckDuckGoProvider",
    "SearchProvider",
    "SearchResult",
    "TavilyProvider",
    "make_search_provider",
]


def make_search_provider(config: WebToolsConfig, client: httpx.AsyncClient) -> SearchProvider:
    """Build the configured provider, sharing the adapter's one HTTP client."""
    provider = config.web_search_provider
    if provider == SearchProviderName.BRAVE:
        if not config.brave_api_key:
            raise ValueError("web_search provider 'brave' requires BRAVE_API_KEY")
        return BraveProvider(client, config.brave_api_key, config.brave_search_url)
    if provider == SearchProviderName.TAVILY:
        if not config.tavily_api_key:
            raise ValueError("web_search provider 'tavily' requires TAVILY_API_KEY")
        return TavilyProvider(client, config.tavily_api_key, config.tavily_search_url)
    return DuckDuckGoProvider(client, config.duckduckgo_search_url, config.web_search_user_agent)
