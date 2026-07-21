"""Builtin tools hosted by ``BuiltinToolAdapter``.

``BUILTIN_DEFINITIONS`` is the aggregated schema source across builtin domains.
Web-tool building blocks (config, egress guard, extraction, search) live in the
:mod:`arcana.tools.builtins.web` subpackage and are re-exported here for
convenience.
"""

from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.builtins.web import (
    FETCH_URL,
    WEB_SEARCH,
    EgressBlocked,
    GuardedResponse,
    SearchProvider,
    SearchProviderName,
    SearchResult,
    WebToolsConfig,
    extract_text,
    guarded_get,
    make_search_provider,
)

__all__ = [
    "BUILTIN_DEFINITIONS",
    "FETCH_URL",
    "WEB_SEARCH",
    "EgressBlocked",
    "GuardedResponse",
    "SearchProvider",
    "SearchProviderName",
    "SearchResult",
    "WebToolsConfig",
    "extract_text",
    "guarded_get",
    "make_search_provider",
]
