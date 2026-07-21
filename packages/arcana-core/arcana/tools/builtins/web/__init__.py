"""Builtin **web** tools: ``web_search`` + ``fetch_url``.

Groups everything specific to network egress — schemas, config, the SSRF/egress
guard, main-text extraction, and the swappable search providers — so other
builtin domains (e.g. filesystem tools) can live as sibling packages under
``builtins`` without entangling with this one.
"""

from arcana.tools.builtins.web.config import (
    SearchProviderName,
    WebToolsConfig,
    WebToolsTunables,
)
from arcana.tools.builtins.web.definitions import FETCH_URL, WEB_SEARCH
from arcana.tools.builtins.web.egress import EgressBlocked, GuardedResponse, guarded_get
from arcana.tools.builtins.web.extract import extract_text
from arcana.tools.builtins.web.search import SearchProvider, SearchResult, make_search_provider

__all__ = [
    "FETCH_URL",
    "WEB_SEARCH",
    "EgressBlocked",
    "GuardedResponse",
    "SearchProvider",
    "SearchProviderName",
    "SearchResult",
    "WebToolsConfig",
    "WebToolsTunables",
    "extract_text",
    "guarded_get",
    "make_search_provider",
]
